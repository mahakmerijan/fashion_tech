"""
Wardrobe Analysis Service
─────────────────────────
Pipeline per uploaded image:
1. Check Redis cache (image hash)
2. Compress to ≤1024px
3. Gemini Vision → structured clothing metadata JSON
4. Store metadata + generate CLIP text embedding
5. Save to Qdrant vector DB
6. Cache → return
"""

import io
import json
import logging
from typing import Optional

import numpy as np
from PIL import Image
from google import genai
from google.genai import types

from app.core.config import get_settings
from app.services.cache_service import (
    cache_get, cache_set, hash_image_bytes,
)

settings = get_settings()
logger = logging.getLogger(__name__)

_client = None

def get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client

WARDROBE_SCHEMA_PROMPT = """Analyse this clothing item image and return a JSON object describing it.

Return ONLY valid JSON with these fields:
{
  "category": "Shirt|Pants|Shoes|Jacket|Tie|Watch|Dress|Shorts|Kurta|Saree|Accessories|Outerwear|Sweater|Belt|Bag|Other",
  "sub_category": "e.g. Oxford Button-Down, Chinos, Chelsea Boots, etc.",
  "primary_color": "main dominant colour name",
  "secondary_colors": ["list", "of", "other", "colours"],
  "pattern": "Solid|Striped|Checked|Plaid|Floral|Geometric|Abstract|Animal Print|Logo|Other",
  "estimated_fit": "Slim Fit|Regular Fit|Loose Fit|Oversized",
  "detected_fabric": "Cotton|Linen|Denim|Wool|Silk|Polyester|Knit|Leather|Synthetic|Unknown",
  "material_weight": "Lightweight|Medium|Heavy",
  "style_vibe": "Casual|Smart Casual|Business|Formal|Streetwear|Sporty|Party|Traditional|Beach",
  "formality_score": 0.0,
  "seasonality": ["Spring", "Summer", "Autumn", "Winter"],
  "brand": "brand name if visible, else null",
  "occasion_suitability": ["Interview", "Casual", "Party", "Office", "Wedding", "Date", "Travel"],
  "condition": "New|Good|Fair|Old",
  "visual_confidence": 0.95
}

Be precise about colours (e.g. 'Navy Blue' not just 'Blue'). formality_score is 0.0 (very casual) to 1.0 (very formal)."""


def compress_image(data: bytes, max_size: int = 1024) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    scale = min(1.0, max_size / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def analyze_wardrobe_item(image_bytes: bytes) -> dict:
    """Analyse a single wardrobe image with Gemini Vision. Cached by image hash."""
    img_hash = hash_image_bytes(image_bytes)
    cache_key = f"wardrobe:analysis:{img_hash}"

    cached = await cache_get(cache_key)
    if cached:
        logger.info("Wardrobe analysis cache hit %s", img_hash[:8])
        return cached if isinstance(cached, dict) else json.loads(cached)

    compressed = compress_image(image_bytes, settings.MAX_IMAGE_SIZE_PX)

    try:
        client = get_client()
        img_part = types.Part.from_bytes(data=compressed, mime_type="image/jpeg")
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL_ANALYZE,
            contents=[WARDROBE_SCHEMA_PROMPT, img_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        metadata: dict = json.loads(response.text)
    except Exception as e:
        logger.error("Gemini wardrobe analysis failed: %s", e)
        metadata = {
            "category": "Unknown",
            "sub_category": None,
            "primary_color": "Unknown",
            "secondary_colors": [],
            "pattern": "Solid",
            "estimated_fit": "Regular Fit",
            "detected_fabric": "Unknown",
            "material_weight": "Medium",
            "style_vibe": "Casual",
            "formality_score": 0.3,
            "seasonality": ["Spring", "Summer", "Autumn", "Winter"],
            "brand": None,
            "occasion_suitability": ["Casual"],
            "condition": "Good",
            "visual_confidence": 0.0,
        }

    # Cache wardrobe analysis (7 days — rarely changes)
    await cache_set(cache_key, metadata, settings.CACHE_TTL_IMAGE)
    return metadata


async def analyze_wardrobe_batch(images: list[tuple[str, bytes]]) -> list[dict]:
    """
    Analyse multiple wardrobe images.
    images: list of (filename, bytes) tuples.
    Returns list of metadata dicts.
    """
    results = []
    for filename, data in images:
        meta = await analyze_wardrobe_item(data)
        meta["_filename"] = filename
        results.append(meta)
    return results


def build_clip_text_description(metadata: dict) -> str:
    """Build a natural language description for CLIP embedding."""
    parts = [
        metadata.get("primary_color", ""),
        metadata.get("detected_fabric", ""),
        metadata.get("estimated_fit", ""),
        metadata.get("sub_category") or metadata.get("category", ""),
        metadata.get("pattern", ""),
        metadata.get("style_vibe", ""),
    ]
    return " ".join(p for p in parts if p and p != "Unknown").strip()


async def generate_clip_embedding(text: str) -> Optional[list[float]]:
    """Generate CLIP text embedding for semantic wardrobe search."""
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("clip-ViT-B-32")
        embedding = model.encode(text, normalize_embeddings=True)
        return embedding.tolist()
    except Exception as e:
        logger.warning("CLIP embedding failed: %s", e)
        return None
