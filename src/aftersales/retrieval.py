"""Version-scoped Chinese BM25 + PostgreSQL pgvector, combined by RRF."""

import hashlib
import math
from collections import Counter
from functools import lru_cache

import httpx
import jieba
from sqlalchemy import delete, select

from .agent_models import PolicyChunk
from .business_models import PolicyVersion
from .domain import DomainError, _unit_context


class EmbeddingUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=2)
def local_embedding_model(name, cache_dir):
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=name, cache_dir=cache_dir, threads=2)


class Embeddings:
    def __init__(self, settings):
        self.settings = settings
        self.name = f"{settings.embedding_provider}:{settings.embedding_model}"
        self.dimension = settings.embedding_dimension

    def embed(self, texts):
        settings = self.settings
        try:
            if settings.embedding_provider == "fastembed":
                vectors = [v.tolist() for v in local_embedding_model(
                    settings.embedding_model, settings.embedding_cache_dir).embed(texts)]
            elif settings.embedding_provider == "openai":
                if not settings.embedding_api_key:
                    raise EmbeddingUnavailable("EMBEDDING_API_KEY missing")
                with httpx.Client(trust_env=False) as client:
                    response = client.post(settings.embedding_api_base.rstrip("/") + "/embeddings",
                        headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
                        json={"model": settings.embedding_model, "input": texts}, timeout=30)
                    response.raise_for_status()
                    vectors = [item["embedding"] for item in sorted(response.json()["data"], key=lambda x: x["index"])]
            elif settings.embedding_provider == "mock" and settings.app_mode == "demo":
                # Explicit deterministic test-only vector generator, not semantic embeddings.
                vectors = []
                for text in texts:
                    raw = hashlib.sha256(text.encode()).digest()
                    vectors.append([(raw[i % len(raw)] - 127) / 128 for i in range(self.dimension)])
            else:
                raise EmbeddingUnavailable("Unsupported embedding provider")
            if len(vectors) != len(texts) or any(len(v) != self.dimension or not all(math.isfinite(x) for x in v) for v in vectors):
                raise EmbeddingUnavailable("Embedding model/dimension mismatch")
            return vectors
        except EmbeddingUnavailable:
            raise
        except Exception as exc:
            raise EmbeddingUnavailable(type(exc).__name__) from None


def index_policies(factory, settings, *, rebuild=False):
    embedder = Embeddings(settings)
    with factory() as session:
        policies = session.scalars(select(PolicyVersion)).all()
        records = [(p.id, p.sections) for p in policies]
    total = 0
    for version_id, sections in records:
        with factory() as session:
            existing = session.scalars(select(PolicyChunk).where(PolicyChunk.version_id == version_id)).all()
            if existing and any(c.embedding_model != embedder.name or c.embedding_dimension != embedder.dimension for c in existing) and not rebuild:
                raise EmbeddingUnavailable("Embedding identity changed; explicit --rebuild required")
            if existing and not rebuild:
                continue
        vectors = embedder.embed([section["text"] for section in sections])
        with factory.begin() as session:
            if rebuild:
                session.execute(delete(PolicyChunk).where(PolicyChunk.version_id == version_id))
            for section, vector in zip(sections, vectors, strict=True):
                session.add(PolicyChunk(version_id=version_id, section_id=section["id"], text=section["text"],
                    embedding=vector, embedding_model=embedder.name, embedding_dimension=embedder.dimension))
                total += 1
    return {"chunks_indexed": total, "embedding_model": embedder.name, "dimension": embedder.dimension}


def bm25(texts, question):
    documents = [list(jieba.cut(text)) for text in texts]
    query = list(jieba.cut(question))
    if not documents:
        return []
    avg_length = sum(map(len, documents)) / len(documents) or 1
    scores = []
    for document in documents:
        counts, score = Counter(document), 0.0
        for word in query:
            df = sum(word in candidate for candidate in documents)
            inverse = math.log(1 + (len(documents) - df + .5) / (df + .5))
            tf = counts[word]
            score += inverse * tf * 2.5 / (tf + 1.5 * (.25 + .75 * len(document) / avg_length))
        scores.append(score)
    return scores


def search_policy(factory, settings, customer_id, unit_id, question):
    if not isinstance(question, str) or not 1 <= len(question) <= 1000:
        raise DomainError(422, "POLICY_QUESTION_INVALID")
    with factory() as session:
        unit, item, order, product = _unit_context(session, unit_id, customer_id)
        policy = session.get(PolicyVersion, unit.sale_policy_version_id)
        if not policy or product.category not in policy.scope.get("categories", []):
            return {"ok": False, "data": None, "reason_codes": ["POLICY_NEEDS_REVIEW"], "source_refs": []}
        version, version_id = policy.version, policy.id
        chunks = session.scalars(select(PolicyChunk).where(PolicyChunk.version_id == version_id)).all()
        if not chunks:
            raise EmbeddingUnavailable("Policy not indexed; run index-policies")
        embedder = Embeddings(settings)
        if any(chunk.embedding_model != embedder.name or chunk.embedding_dimension != embedder.dimension for chunk in chunks):
            raise EmbeddingUnavailable("Stored embedding identity/dimension mismatch")
        lexical = bm25([chunk.text for chunk in chunks], question)
        lexical_rank = sorted(range(len(chunks)), key=lambda i: lexical[i], reverse=True)
        lexical_ids = [chunks[i].id for i in lexical_rank if lexical[i] > 0][:12]
        texts = {chunk.id: {"version": version, "section_id": chunk.section_id,
                          "text": chunk.text, "source_title": f"演示商家政策 {version}"} for chunk in chunks}
    vector = Embeddings(settings).embed([question])[0]
    with factory() as session:
        vector_ids = list(session.scalars(select(PolicyChunk.id).where(
            PolicyChunk.version_id == version_id, PolicyChunk.embedding_model == embedder.name,
            PolicyChunk.embedding_dimension == embedder.dimension,
        ).order_by(PolicyChunk.embedding.cosine_distance(vector)).limit(12)))
    scores: dict[str, float] = {}
    for ranking in [lexical_ids, vector_ids]:
        for rank, chunk_id in enumerate(ranking, 1):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (60 + rank)
    selected = sorted(scores, key=lambda key: scores[key], reverse=True)[:6]
    return {"ok": True, "data": [texts[key] for key in selected], "reason_codes": [],
            "source_refs": [{"version": version, "section_id": texts[key]["section_id"]} for key in selected]}
