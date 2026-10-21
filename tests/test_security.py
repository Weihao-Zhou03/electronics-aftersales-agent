"""Abuse limits and evidence isolation; fixtures are explicitly synthetic unless marked PG."""

import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select
from test_agent_postgres import pg_factory  # noqa: F401 - shared isolated real PostgreSQL fixture
from test_identity import context, login  # noqa: F401 - isolated synthetic app fixture

from aftersales.db import init_db
from aftersales.limits import consume
from aftersales.models import RateBucket


def test_private_image_type_and_customer_scope(context, tmp_path):  # noqa: F811 - pytest injects imported fixture
    client, factory, adapter = context
    client.app.state.settings = replace(client.app.state.settings, attachment_storage_path=str(tmp_path))
    headers = login(client, "customer-a")
    assert client.post("/api/v1/attachments", headers=headers,
                       files={"file": ("fake.png", b"<script>not an image</script>", "image/png")}).status_code == 422
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "green").save(buffer, format="PNG")
    response = client.post("/api/v1/attachments", headers=headers,
                           files={"file": ("../../escape.png", buffer.getvalue(), "image/png")})
    assert response.status_code == 200
    path = "/api/v1/attachments/" + response.json()["id"]
    assert client.get(path).status_code == 200
    login(client, "customer-b")
    assert client.get(path).status_code == 404


@pytest.mark.postgres
def test_actual_postgres_atomic_rate_counter_caps_concurrent_attempts(pg_factory):  # noqa: F811 - pytest injects imported fixture
    factory, _url = pg_factory
    init_db(factory)
    def attempt(_):
        try:
            with factory.begin() as session:
                consume(session, "login-test", "synthetic-subject", limit=5, now=1800000000)
            return True
        except HTTPException as exc:
            assert exc.status_code == 429
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(20)))
    assert sum(results) == 5
    with factory() as session:
        assert session.scalar(select(RateBucket.count)) == 5
