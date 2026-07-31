"""
POST /api/products/from-feedback   — extract items from feedback text
POST /api/products/detect-from-image — detect items in generated image not in wardrobe
"""
import json
import re
import logging
import pathlib
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings
from app.services.image_generator import get_client
from app.services.shopping_aggregator import search_all_platforms
from google.genai import types as gtypes

router_feedback = APIRouter(prefix="/api/products", tags=["products"])
settings = get_settings()
logger = logging.getLogger(__name__)


class FeedbackShoppingRequest(BaseModel):
    feedback: str
    gender: Optional[str] = "men"


class DetectFromImageRequest(BaseModel):
    image_url: str
    wardrobe: list[dict] = []
    gender: Optional[str] = "men"


@router_feedback.post("/from-feedback")
async def shopping_from_feedback(body: FeedbackShoppingRequest):
    if not body.feedback or not body.feedback.strip():
        return {"items": []}

    extraction_prompt = f"""The user gave this feedback about their outfit image: "{body.feedback}"
Extract any clothing items, accessories, or fashion pieces the user wants added or changed.
Return ONLY a JSON array of objects with "category" and "description" fields.
Example: [{{"category": "Accessories", "description": "stylish leather gloves"}}]
If no specific items are mentioned, return [].
Return ONLY the JSON array, no explanation."""

    try:
        client = get_client()
        resp = client.models.generate_content(
            model=settings.GEMINI_MODEL_RECOMMEND,
            contents=extraction_prompt,
            config=gtypes.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.1, max_output_tokens=256),
        )
        extracted = json.loads(resp.text)
        if not isinstance(extracted, list):
            extracted = []
    except Exception as e:
        logger.error("Failed to extract items from feedback: %s", e)
        return {"items": []}

    if not extracted:
        return {"items": []}

    gender_str = body.gender or "men"
    all_results = []
    for item in extracted[:3]:
        category = item.get("category", "clothing")
        description = item.get("description", "")
        color_match = re.search(
            r"\b(navy|black|white|grey|gray|brown|beige|red|blue|green|cream|khaki|olive|tan|leather|dark|light)\b",
            description, re.IGNORECASE)
        color = color_match.group(1).lower() if color_match else ""
        results = await search_all_platforms(category=category, color=color, fit="regular",
                                              gender=gender_str, description=description)
        for r in results:
            r["for_item"] = description
            r["from_feedback"] = True
        all_results.extend(results)

    return {"items": all_results, "extracted": extracted}


@router_feedback.post("/detect-from-image")
async def detect_from_image(body: DetectFromImageRequest):
    """
    Analyze the generated outfit image with Gemini vision.
    Detect all items visible → compare vs wardrobe → return shopping for unmatched items.
    """
    image_path: Optional[pathlib.Path] = None
    if body.image_url.startswith("/static/"):
        local = pathlib.Path("/tmp/styleai") / body.image_url.removeprefix("/static/")
        if local.exists():
            image_path = local

    if image_path is None:
        return {"items": [], "detected": []}

    try:
        image_bytes = image_path.read_bytes()
    except Exception as e:
        logger.warning("Could not read generated image: %s", e)
        return {"items": [], "detected": []}

    gender_str = (body.gender or "men").lower()

    wardrobe_desc = "; ".join(
        f"{w.get('primary_color', '')} {w.get('sub_category') or w.get('category', '')}".strip()
        for w in body.wardrobe
    ) or "none"

    detection_prompt = f"""Analyze this fashion outfit image.

List every distinct clothing item and accessory you can see (shirt, trousers, shoes, belt, bag, glasses, gloves, jacket, etc.).

The person already owns these wardrobe items: {wardrobe_desc}

Return ONLY a JSON array of items that are visible in the image but NOT clearly covered by the wardrobe list above.
Each object: {{"category": "...", "description": "specific description with color", "color": "..."}}
Example: [{{"category": "Bag", "description": "tan leather crossbody bag", "color": "tan"}}]
Return [] if all visible items match the wardrobe. Return ONLY the JSON array."""

    try:
        client = get_client()
        resp = client.models.generate_content(
            model=settings.GEMINI_MODEL_ANALYZE,
            contents=[
                detection_prompt,
                gtypes.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            ],
            config=gtypes.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.1, max_output_tokens=512),
        )
        detected = json.loads(resp.text)
        if not isinstance(detected, list):
            detected = []
    except Exception as e:
        logger.error("Image item detection failed: %s", e)
        return {"items": [], "detected": []}

    if not detected:
        return {"items": [], "detected": []}

    all_results = []
    for item in detected[:4]:
        category = item.get("category", "clothing")
        description = item.get("description", "")
        color = item.get("color", "")
        results = await search_all_platforms(category=category, color=color, fit="regular",
                                              gender=gender_str, description=description)
        for r in results:
            r["for_item"] = description
            r["from_image"] = True
        all_results.extend(results)

    return {"items": all_results, "detected": detected}
