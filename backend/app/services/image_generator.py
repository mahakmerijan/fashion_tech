"""
Image Generation Service — Gemini Image
────────────────────────────────────────
Pipeline:
1. Build deterministic prompt hash (user_face + outfit_items)
2. Check Redis image cache → if hit, return CDN URL immediately
3. Check PostgreSQL generated_images table as secondary cache
4. Build Gemini image generation prompt
5. Call Gemini image generation API
6. Upload to S3/R2
7. Store in DB + Redis cache
8. Return CDN URL

Token optimisation: same outfit + face combo never re-generated.
"""

import io
import hashlib
import json
import logging
import base64
from typing import Optional

from google import genai
from google.genai import types as gtypes

from app.core.config import get_settings
from app.services.cache_service import (
    cache_get, cache_set, image_cache_key, hash_outfit_prompt,
)

settings = get_settings()
logger = logging.getLogger(__name__)

_client = None

def get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def build_image_prompt(face_profile: dict, outfit_items: list[dict], occasion: str) -> str:
    """Build descriptive virtual try-on prompt for Gemini Image."""
    gender_raw = (face_profile.get("gender") or "").lower()
    if gender_raw in ("female", "woman", "girl"):
        subject = "woman"
    elif gender_raw in ("others", "non-binary"):
        subject = "person"
    else:
        subject = "man"   # default to male when gender is Male or unset

    face_desc = (
        f"{subject} with {face_profile.get('face_shape', 'oval')} face shape, "
        f"{face_profile.get('skin_tone', 'medium')} skin tone, "
        f"{face_profile.get('hair_color', 'dark')} hair"
    )
    outfit_parts = []
    for item in outfit_items:
        outfit_parts.append(
            f"{item.get('color', '')} {item.get('description', item.get('category', 'item'))}"
        )
    outfit_desc = ", ".join(outfit_parts)

    return (
        f"Fashion editorial photo of a {subject}. {face_desc}. "
        f"IMPORTANT: This is a {subject} — do NOT change the gender. "
        f"Wearing: {outfit_desc}. "
        f"Occasion: {occasion}. "
        f"Full body shot, neutral studio background, professional photography, "
        f"high quality fashion magazine style, well-lit, sharp details."
    )


async def generate_outfit_image(
    user_id: str,
    outfit_id: str,
    face_profile: dict,
    outfit_items: list[dict],
    occasion: str = "Casual",
    selfie_b64: Optional[str] = None,
    place_b64: Optional[str] = None,
    user_feedback: Optional[str] = None,
    db_session=None,
) -> dict:
    """
    Generate or retrieve cached virtual try-on image.
    If selfie_b64 is provided, uses the user's actual face as a reference.
    Returns dict with 'image_url' and 'from_cache' flag.
    """
    # 1. Build deterministic hash (include whether selfie was provided + feedback)
    selfie_flag = "selfie" if selfie_b64 else "nophoto"
    feedback_flag = user_feedback.strip()[:120] if user_feedback else ""
    prompt_hash = hash_outfit_prompt(
        {k: face_profile.get(k) for k in ["face_shape", "skin_tone", "hair_color", "eye_color"]},
        [{"item_id": i.get("item_id", ""), "description": i.get("description", ""), "flag": selfie_flag, "fb": feedback_flag} for i in outfit_items],
    )
    cache_key = image_cache_key(prompt_hash)

    # 2. Redis cache check
    cached = await cache_get(cache_key)
    if cached:
        logger.info("Image cache HIT for hash %s", prompt_hash[:8])
        url = cached if isinstance(cached, str) else cached.get("image_url", "")
        return {"image_url": url, "from_cache": True, "prompt_hash": prompt_hash}

    # 3. Check DB (secondary cache)
    if db_session:
        from sqlalchemy import select
        from app.db.models import GeneratedImage
        result = await db_session.execute(
            select(GeneratedImage).where(GeneratedImage.prompt_hash == prompt_hash)
        )
        existing = result.scalar_one_or_none()
        if existing:
            await cache_set(cache_key, existing.image_url, settings.CACHE_TTL_IMAGE)
            return {"image_url": existing.image_url, "from_cache": True, "prompt_hash": prompt_hash}

    # 4. Build prompt + decode selfie if provided
    selfie_bytes: Optional[bytes] = None
    if selfie_b64:
        try:
            raw_b64 = selfie_b64.split(",", 1)[-1]
            selfie_bytes = base64.b64decode(raw_b64)
        except Exception as e:
            logger.warning("Failed to decode selfie_b64: %s", e)
            selfie_bytes = None

    place_bytes: Optional[bytes] = None
    if place_b64:
        try:
            raw_b64 = place_b64.split(",", 1)[-1]
            place_bytes = base64.b64decode(raw_b64)
        except Exception as e:
            logger.warning("Failed to decode place_b64: %s", e)
            place_bytes = None

    gender_raw = (face_profile.get("gender") or "").lower()
    if gender_raw in ("female", "woman", "girl"):
        subject = "woman"
    elif gender_raw in ("others", "non-binary"):
        subject = "person"
    else:
        subject = "man"

    outfit_parts = [
        f"{i.get('color', '')} {i.get('description', i.get('category', 'item'))}"
        for i in outfit_items
    ]
    outfit_desc = ", ".join(p for p in outfit_parts if p)
    style_hint = face_profile.get("style_personality", "")
    color_season = face_profile.get("color_season", "")
    style_ctx = " | ".join(filter(None, [
        f"Style: {style_hint}" if style_hint else "",
        f"Colour season: {color_season}" if color_season else "",
    ]))
    feedback_phrase = f"\nUser feedback: {user_feedback.strip()}." if user_feedback and user_feedback.strip() else ""

    # Fetch actual dress images from wardrobe item URLs (concurrent)
    import asyncio as _asyncio
    import httpx as _httpx

    async def _fetch_dress(url: str) -> Optional[bytes]:
        if not url or not url.startswith("http") or "localhost" in url:
            return None
        try:
            async with _httpx.AsyncClient(timeout=8) as c:
                r = await c.get(url, headers={"User-Agent": "StyleAI/1.0"})
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                    return r.content
        except Exception:
            pass
        return None

    dress_urls = [i.get("image_url", "") for i in outfit_items if i.get("image_url")]
    if dress_urls:
        fetched_raw = await _asyncio.gather(*[_fetch_dress(u) for u in dress_urls], return_exceptions=True)
        from PIL import Image as _PILImage
        import io as _io
        dress_images: list[bytes] = []
        for f in fetched_raw:
            if isinstance(f, bytes) and len(f) > 1000:
                try:
                    pil = _PILImage.open(_io.BytesIO(f)).convert("RGB")
                    w, h = pil.size
                    scale = min(1.0, 512 / max(w, h))
                    if scale < 1.0:
                        pil = pil.resize((int(w * scale), int(h * scale)), _PILImage.LANCZOS)
                    buf = _io.BytesIO()
                    pil.save(buf, format="JPEG", quality=85)
                    dress_images.append(buf.getvalue())
                except Exception:
                    pass
    else:
        dress_images = []

    # Anti-hallucination: detect outfit type and build structured prompt
    outfit_lower = outfit_desc.lower()
    is_traditional = any(w in outfit_lower for w in ["kurta", "churidar", "dhoti", "sherwani", "ethnic", "traditional", "bandhgala"])
    if is_traditional:
        clothing_note = "OUTFIT TYPE: Traditional/ethnic Indian wear as listed. Render exactly these garments."
    else:
        clothing_note = (
            "OUTFIT TYPE: Modern western fashion. "
            "CRITICAL: Show ONLY the exact clothing listed. "
            "DO NOT substitute based on occasion/location/culture. "
            "DO NOT generate kurta/churidar/ethnic wear."
        )

    # Build reference image instruction list
    ref_lines = []
    img_idx = 1
    if selfie_bytes:
        ref_lines.append(f"IMAGE {img_idx} (selfie): match this exact person's face, skin tone, hair — do NOT alter")
        img_idx += 1
    for n in range(len(dress_images)):
        ref_lines.append(f"IMAGE {img_idx + n}: actual garment {n+1} — render this EXACT clothing on the person")
    img_idx += len(dress_images)
    if place_bytes:
        ref_lines.append(f"IMAGE {img_idx} (venue): place person naturally in this setting with matching lighting")
    ref_block = "\n".join(f"  - {r}" for r in ref_lines) if ref_lines else ""

    if selfie_bytes or dress_images or place_bytes:
        prompt = (
            f"Fashion editorial photograph of a {subject}.\n\n"
            f"EXACT CLOTHING TO RENDER:\n  {outfit_desc}\n{feedback_phrase}\n"
            f"{clothing_note}\n\n"
            + (f"Reference images:\n{ref_block}\n\n" if ref_block else "")
            + f"RULES: preserve selfie face/skin/hair, show exact listed garments, "
            f"natural anatomy head-to-body ~1:7, single seamless photo.\n"
            f"Style: {style_ctx}\nOccasion: {occasion}\n"
            f"Full body, professional fashion photography."
        )
    else:
        prompt = build_image_prompt(face_profile, outfit_items, occasion)

    logger.info("Generating image hash=%s selfie=%s dresses=%d place=%s",
                prompt_hash[:8], bool(selfie_bytes), len(dress_images), bool(place_bytes))

    # 5. Call Gemini Image Generation
    models_to_try = [settings.GEMINI_MODEL_IMAGE] + [
        m.strip() for m in settings.GEMINI_MODEL_IMAGE_FALLBACKS.split(",") if m.strip()
    ]
    image_data: Optional[bytes] = None
    last_error: Optional[Exception] = None

    client = get_client()
    for model in models_to_try:
        try:
            if selfie_bytes or dress_images or place_bytes:
                contents = [prompt]
                if selfie_bytes:
                    contents.append(gtypes.Part.from_bytes(data=selfie_bytes, mime_type="image/jpeg"))
                for d in dress_images:
                    contents.append(gtypes.Part.from_bytes(data=d, mime_type="image/jpeg"))
                if place_bytes:
                    from PIL import Image as PILImage
                    import io as _io
                    pil = PILImage.open(_io.BytesIO(place_bytes)).convert("RGB")
                    w, h = pil.size
                    scale = min(1.0, 1024 / max(w, h))
                    if scale < 1.0:
                        pil = pil.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
                    buf = _io.BytesIO()
                    pil.save(buf, format="JPEG", quality=85)
                    contents.append(gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"))
            else:
                contents = prompt

            resp = client.models.generate_content(
                model=model,
                contents=contents,
                config=gtypes.GenerateContentConfig(
                    response_modalities=["IMAGE"]
                ),
            )
            for part in resp.candidates[0].content.parts:
                if part.inline_data is not None:
                    image_data = part.inline_data.data
                    break
            if image_data:
                logger.info("Image generated with model %s (selfie_ref=%s)", model, bool(selfie_bytes))
                break
        except Exception as e:
            logger.warning("Model %s failed: %s", model, e)
            last_error = e
            # If the model rejects image input, retry without selfie on next model
            selfie_bytes = None
            continue

    if not image_data:
        logger.error("All image models failed. Last error: %s", last_error)
        placeholder = "https://placehold.co/512x768/7c3aed/white?text=Virtual+Try-On"
        await cache_set(cache_key, placeholder, 300)
        return {"image_url": placeholder, "from_cache": False, "prompt_hash": prompt_hash}

    # 6. Upload to S3/R2
    image_url = await upload_image_to_storage(image_data, f"generated/{user_id}/{prompt_hash}.jpg")

    # 7. Persist to DB
    if db_session:
        from app.db.models import GeneratedImage
        import uuid as _uuid
        try:
            uid = str(_uuid.UUID(user_id))  # validate and normalise to string
        except (ValueError, AttributeError):
            uid = None
        gen_img = GeneratedImage(
            user_id=uid,
            prompt_hash=prompt_hash,
            image_url=image_url,
            outfit_description=prompt[:500],
        )
        db_session.add(gen_img)
        await db_session.commit()

    # 8. Cache
    await cache_set(cache_key, image_url, settings.CACHE_TTL_IMAGE)

    return {"image_url": image_url, "from_cache": False, "prompt_hash": prompt_hash}


async def upload_image_to_storage(image_bytes: bytes, key: str) -> str:
    """Upload image to S3/R2 and return public URL."""
    try:
        import boto3
        from botocore.config import Config

        s3 = boto3.client(
            "s3",
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4"),
        )
        s3.put_object(
            Bucket=settings.S3_BUCKET,
            Key=key,
            Body=image_bytes,
            ContentType="image/jpeg",
            CacheControl="max-age=604800",
        )
        base_url = settings.CDN_BASE_URL or f"https://{settings.S3_BUCKET}.s3.{settings.S3_REGION}.amazonaws.com"
        return f"{base_url}/{key}"
    except Exception as e:
        logger.error("S3 upload failed: %s", e)
        # Save locally as fallback (dev mode)
        import os, pathlib
        local_path = pathlib.Path(f"/tmp/styleai/{key}")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(image_bytes)
        return f"/static/{key}"
