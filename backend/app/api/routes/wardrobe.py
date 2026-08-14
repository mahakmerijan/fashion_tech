import uuid
import asyncio
import logging
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.db.database import get_db
from app.db.models import UserProfile, WardrobeItem
from app.services.wardrobe_analyzer import analyze_wardrobe_item, build_clip_text_description, generate_clip_embedding
from app.services.cache_service import cache_delete, profile_context_key
from app.schemas.wardrobe import WardrobeUploadResponse, WardrobeListResponse, WardrobeItemOut

router = APIRouter(prefix="/api/wardrobe", tags=["wardrobe"])
logger = logging.getLogger(__name__)

MAX_IMAGES_PER_UPLOAD = 50  # per batch-chunk — frontend sends 5 at a time
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB

# Limit concurrent Gemini Vision calls to avoid rate limits (free tier = 15 RPM)
# 3 concurrent × ~4s each = ~4s per batch of 5 images
_GEMINI_SEMAPHORE = asyncio.Semaphore(3)


async def _process_single_image(image: UploadFile, user_id: str) -> WardrobeItem | None:
    """Analyse one image and build a WardrobeItem — rate-limited by semaphore."""
    if not image.content_type or not image.content_type.startswith("image/"):
        logger.warning("Skipping non-image file: %s (%s)", image.filename, image.content_type)
        return None
    data = await image.read()
    if len(data) > MAX_IMAGE_SIZE:
        logger.warning("Skipping oversized image: %s (%d bytes)", image.filename, len(data))
        return None
    try:
        # Semaphore limits concurrent Gemini calls to 3 — prevents 429 rate limit errors
        async with _GEMINI_SEMAPHORE:
            metadata = await analyze_wardrobe_item(data)

        text_desc = build_clip_text_description(metadata)
        embedding = await generate_clip_embedding(text_desc)
        item_id = str(uuid.uuid4())
        image_key = f"wardrobe/{user_id}/{item_id}.jpg"
        image_url = await _store_image(data, image_key)
        return WardrobeItem(
            item_id=item_id,
            user_id=user_id,
            image_url=image_url,
            category=metadata.get("category", "Unknown"),
            sub_category=metadata.get("sub_category"),
            primary_color=metadata.get("primary_color"),
            secondary_colors=metadata.get("secondary_colors", []),
            pattern=metadata.get("pattern", "Solid"),
            estimated_fit=metadata.get("estimated_fit"),
            detected_fabric=metadata.get("detected_fabric"),
            material_weight=metadata.get("material_weight"),
            formality_score=metadata.get("formality_score"),
            ai_metadata=metadata,
            embedding=embedding,
        )
    except Exception as e:
        logger.error("Failed to process image %s: %s", image.filename, e)
        return None


@router.post("/{user_id}/upload", response_model=WardrobeUploadResponse)
async def upload_wardrobe(
    user_id: str,
    images: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload one or more wardrobe images. AI analyses all concurrently."""
    if len(images) > MAX_IMAGES_PER_UPLOAD:
        raise HTTPException(status_code=400, detail=f"Max {MAX_IMAGES_PER_UPLOAD} images per upload")

    try:
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    # Analyse ALL images concurrently instead of sequentially
    results = await asyncio.gather(
        *[_process_single_image(img, user_id) for img in images],
        return_exceptions=True,
    )

    items_created = [r for r in results if isinstance(r, WardrobeItem)]

    for item in items_created:
        db.add(item)

    await db.commit()
    for item in items_created:
        await db.refresh(item)

    # Invalidate profile context cache (wardrobe changed)
    await cache_delete(profile_context_key(user_id))

    return WardrobeUploadResponse(
        items=[_item_to_out(i) for i in items_created],
        total_uploaded=len(items_created),
    )


@router.get("/{user_id}", response_model=WardrobeListResponse)
async def get_wardrobe(user_id: str, db: AsyncSession = Depends(get_db)):
    try:
        uuid.UUID(user_id)  # validate format
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    result = await db.execute(
        select(WardrobeItem).where(WardrobeItem.user_id == user_id).order_by(WardrobeItem.created_at.desc())
    )
    items = result.scalars().all()
    return WardrobeListResponse(items=[_item_to_out(i) for i in items], total=len(items))


@router.delete("/{user_id}/items/{item_id}")
async def delete_wardrobe_item(user_id: str, item_id: str, db: AsyncSession = Depends(get_db)):
    try:
        uuid.UUID(user_id)  # validate format
        uuid.UUID(item_id)  # validate format
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    result = await db.execute(
        select(WardrobeItem).where(WardrobeItem.item_id == item_id, WardrobeItem.user_id == user_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    await db.delete(item)
    await db.commit()

    # Invalidate profile context cache
    await cache_delete(profile_context_key(user_id))

    return {"deleted": True, "item_id": item_id}


async def _store_image(data: bytes, key: str) -> str:
    """
    Store image bytes.
    Priority: S3 (if configured) → base64 data URL in DB (persistent, no filesystem).
    """
    from app.core.config import get_settings
    import base64
    import io
    from PIL import Image as PILImage

    s = get_settings()

    # Try S3 if credentials are set
    if s.AWS_ACCESS_KEY_ID:
        try:
            import boto3
            s3 = boto3.client("s3", region_name=s.S3_REGION,
                              aws_access_key_id=s.AWS_ACCESS_KEY_ID,
                              aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY)
            s3.put_object(Bucket=s.S3_BUCKET, Key=key, Body=data, ContentType="image/jpeg")
            base = s.CDN_BASE_URL or f"https://{s.S3_BUCKET}.s3.{s.S3_REGION}.amazonaws.com"
            return f"{base}/{key}"
        except Exception:
            pass

    # Fallback: compress to thumbnail and store as base64 data URL in the DB.
    # This is persistent (SQLite) and requires no external storage.
    try:
        img = PILImage.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((300, 300), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        pass

    # Last resort: local /tmp (dev only)
    import pathlib
    p = pathlib.Path(f"/tmp/styleai/{key}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return f"/static/{key}"


def _item_to_out(item: WardrobeItem) -> WardrobeItemOut:
    return WardrobeItemOut(
        item_id=str(item.item_id),
        image_url=item.image_url,
        category=item.category,
        sub_category=item.sub_category,
        primary_color=item.primary_color,
        secondary_colors=item.secondary_colors,
        pattern=item.pattern,
        estimated_fit=item.estimated_fit,
        detected_fabric=item.detected_fabric,
        formality_score=item.formality_score,
        style_vibe=item.ai_metadata.get("style_vibe") if item.ai_metadata else None,
        ai_metadata=item.ai_metadata,
    )
