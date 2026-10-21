"""Private, authenticated evidence images; no URL fetching or public file serving."""

import hashlib
import io
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from .agent_models import EvidenceAttachment
from .auth import current_user, write_user

router = APIRouter(prefix="/api/v1/attachments")
MAX_SIZE = 5 * 1024 * 1024
TYPES = {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg"), "WEBP": ("image/webp", ".webp")}


def validate_evidence(session, owner_id, attachment_ids):
    if not isinstance(attachment_ids, list) or len(attachment_ids) > 4 or len(set(attachment_ids)) != len(attachment_ids):
        raise HTTPException(422, "At most four distinct evidence images")
    found = list(session.scalars(select(EvidenceAttachment.id).where(
        EvidenceAttachment.owner_id == owner_id, EvidenceAttachment.id.in_(attachment_ids))))
    if set(found) != set(attachment_ids):
        raise HTTPException(404, "Evidence not accessible")


@router.post("")
async def upload(request: Request, file: UploadFile = File(), user=Depends(write_user)):
    if user.role != "customer":
        raise HTTPException(403, "Customer role required")
    from .limits import limit_request
    limit_request(request.app.state.session_factory, "image-upload", user.user_id, limit=10)
    content = await file.read(MAX_SIZE + 1)
    if not content or len(content) > MAX_SIZE:
        raise HTTPException(413, "Images must be no larger than 5 MB")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in TYPES or image.width * image.height > 25_000_000:
                raise ValueError()
            mime, extension = TYPES[image.format]
            image.verify()
        if file.content_type != mime:
            raise ValueError()
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        raise HTTPException(422, "A valid PNG, JPEG or WebP image is required") from None
    root = Path(request.app.state.settings.attachment_storage_path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    storage_key = uuid4().hex + extension
    target = root / storage_key
    target.write_bytes(content)
    try:
        with request.app.state.session_factory.begin() as session:
            record = EvidenceAttachment(owner_id=user.user_id, storage_key=storage_key,
                mime=mime, size=len(content), sha256=hashlib.sha256(content).hexdigest())
            session.add(record)
            session.flush()
            result = {"id": record.id, "mime": mime, "size": len(content)}
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return result


@router.get("/{attachment_id}")
def download(attachment_id: str, request: Request, user=Depends(current_user)):
    from .limits import limit_request
    limit_request(request.app.state.session_factory, "image-download", user.user_id, limit=60)
    with request.app.state.session_factory() as session:
        query = select(EvidenceAttachment).where(EvidenceAttachment.id == attachment_id)
        if user.role == "customer":
            query = query.where(EvidenceAttachment.owner_id == user.user_id)
        elif user.role not in {"support", "admin"}:
            raise HTTPException(403, "Role not permitted")
        record = session.scalar(query)
        if not record:
            raise HTTPException(404, "Evidence not accessible")
        root = Path(request.app.state.settings.attachment_storage_path).resolve()
        target = (root / record.storage_key).resolve()
        if target.parent != root or not target.is_file():
            raise HTTPException(404, "Evidence unavailable")
        return FileResponse(target, media_type=record.mime, filename=record.id + target.suffix,
                            content_disposition_type="inline", headers={"Cache-Control": "private, no-store"})
