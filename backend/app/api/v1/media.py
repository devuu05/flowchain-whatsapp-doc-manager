"""Signed, short-lived media URLs fetched by the WhatsApp provider to deliver documents."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import DB
from app.core.errors import NotFound
from app.db.base import utcnow
from app.models import MediaBlob
from app.security.signed_urls import verify_media_token

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/{token}")
def fetch_media(token: str, db: DB) -> Response:
    if len(token) > 600:
        raise NotFound()
    verified = verify_media_token(token)
    if verified is None:
        raise NotFound()
    tenant_id, blob_id = verified
    blob = db.scalar(
        select(MediaBlob).where(
            MediaBlob.id == blob_id,
            MediaBlob.tenant_id == tenant_id,
            MediaBlob.purpose == "delivery",
            MediaBlob.expires_at > utcnow(),
        )
    )
    if blob is None:
        raise NotFound()
    return Response(
        content=blob.data,
        media_type=blob.mime_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(blob.filename)}",
            "Cache-Control": "private, no-store",
            "X-Robots-Tag": "noindex",
        },
    )
