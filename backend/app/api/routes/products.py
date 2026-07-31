"""
Product image generation endpoint.
GET /api/products/image?category=blazer&color=navy&gender=men
Returns a cached Gemini-generated clean product shot.
"""
import hashlib
import pathlib
import logging
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from app.core.config import get_settings
from app.services.image_generator import get_client
from google.genai import types as gtypes

router = APIRouter(prefix="/api/products", tags=["products"])
settings = get_settings()
logger = logging.getLogger(__name__)

PRODUCTS_DIR = pathlib.Path("/tmp/styleai/products")
PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/image")
async def get_product_image(
    category: str = "clothing",
    color: str = "",
    gender: str = "men",
    description: str = "",
):
    """
    Return (or generate) a clean white-background product photo for a clothing item.
    Cached on disk by category+color+gender hash — never regenerated for the same item.
    """
    key = f"{gender}-{color}-{category}-{description[:40]}".lower().replace(" ", "-")
    img_hash = hashlib.sha256(key.encode()).hexdigest()[:16]
    img_path = PRODUCTS_DIR / f"{img_hash}.jpg"

    # Serve from disk cache
    if img_path.exists():
        return FileResponse(str(img_path), media_type="image/jpeg")

    # Generate with Gemini
    subject = "woman" if gender.lower() in ("female", "women", "woman") else "man"
    desc = description or f"{color} {category}".strip()
    prompt = (
        f"Clean product photography of a {desc} on a pure white background. "
        f"Fashion e-commerce style, no model, item laid flat or on a mannequin. "
        f"High quality, sharp details, neutral studio lighting, suitable for online shopping."
    )
    try:
        client = get_client()
        models_to_try = [settings.GEMINI_MODEL_IMAGE] + [
            m.strip() for m in settings.GEMINI_MODEL_IMAGE_FALLBACKS.split(",") if m.strip()
        ]
        image_data: Optional[bytes] = None
        for model in models_to_try:
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=gtypes.GenerateContentConfig(response_modalities=["IMAGE"]),
                )
                for part in resp.candidates[0].content.parts:
                    if part.inline_data is not None:
                        image_data = part.inline_data.data
                        break
                if image_data:
                    break
            except Exception as e:
                logger.warning("Product image model %s failed: %s", model, e)
                continue

        if image_data:
            img_path.write_bytes(image_data)
            return FileResponse(str(img_path), media_type="image/jpeg")

    except Exception as e:
        logger.error("Product image generation failed: %s", e)

    return JSONResponse({"error": "Could not generate product image"}, status_code=503)
