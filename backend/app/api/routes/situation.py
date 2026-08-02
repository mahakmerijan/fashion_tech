"""
Situation-based recommendation route.
POST /api/situation/recommend  — accepts multipart/form-data
"""
import json
import uuid
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models import UserProfile, WardrobeItem
from app.services.situation_service import run_situation_pipeline
from app.services.shopping_aggregator import search_all_platforms

router = APIRouter(prefix="/api/situation", tags=["situation"])


@router.post("/recommend")
async def situation_recommend(
    situation_text: str = Form(...),
    person_description: str = Form(""),
    user_id: str = Form(""),
    face_profile: str = Form("{}"),
    preferences: str = Form("{}"),
    wardrobe_ids: str = Form("[]"),
    wardrobe_meta: str = Form("[]"),
    selfie_b64: str = Form(""),
    place_image: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Full situation-based outfit recommendation pipeline:
    1. Analyse place image (Gemini Vision)
    2. Match wardrobe to situation (rule engine + Gemini reasoning)
    3. Generate composite image — user in the outfit at the place (Gemini Image)
    4. Search shopping sites for missing items (Redis-cached)
    """
    if len(situation_text.strip()) < 5:
        raise HTTPException(status_code=400, detail="situation_text too short")

    # ── Parse JSON fields ──────────────────────────────────────────────────────
    try:
        face = json.loads(face_profile)
    except Exception:
        face = {}
    try:
        prefs = json.loads(preferences)
    except Exception:
        prefs = {}
    try:
        wardrobe_list = json.loads(wardrobe_meta)
    except Exception:
        wardrobe_list = []

    # ── Load profile + wardrobe from DB (overrides inline data if user exists) ──
    if user_id:
        try:
            uuid.UUID(user_id)  # validate format
            result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
            user = result.scalar_one_or_none()
            if user:
                face = {
                    "face_shape": user.face_shape,
                    "skin_tone": user.skin_tone,
                    "eye_color": user.eye_color,
                    "hair_color": user.hair_color,
                    "expression_vibe": user.expression_vibe,
                    "style_personality": user.style_personality,
                    "color_season": user.color_season,
                }
                prefs = {
                    "gender": user.gender or prefs.get("gender", ""),
                    "fit": user.fit_preference,
                    "favorite_colors": user.favorite_colors or [],
                    "fabrics": user.preferred_fabrics or [],
                    "priority": user.priority_feature,
                    "experiment_level": user.experiment_level,
                    # Preserve user-selected style from questionnaire (not in DB)
                    "style_personality": prefs.get("style_personality") or user.style_vibe or "",
                    "sustainability": user.sustainability,
                }
                w_result = await db.execute(
                    select(WardrobeItem).where(WardrobeItem.user_id == user_id)
                )
                wardrobe_list = [
                    {
                        "item_id": str(i.item_id),
                        "category": i.category,
                        "sub_category": i.sub_category,
                        "primary_color": i.primary_color,
                        "estimated_fit": i.estimated_fit,
                        "detected_fabric": i.detected_fabric,
                        "formality_score": i.formality_score or 0.3,
                        "style_vibe": (i.ai_metadata or {}).get("style_vibe"),
                        "image_url": i.image_url,
                    }
                    for i in w_result.scalars().all()
                ]
        except (ValueError, Exception):
            pass

    # ── Read place image bytes ─────────────────────────────────────────────────
    place_bytes: Optional[bytes] = None
    if place_image and place_image.content_type and place_image.content_type.startswith("image/"):
        place_bytes = await place_image.read()
        if len(place_bytes) > 15 * 1024 * 1024:
            place_bytes = None  # too large, skip

    # ── Run LangGraph pipeline ─────────────────────────────────────────────────
    result = await run_situation_pipeline(
        user_id=user_id or "anonymous",
        situation_text=situation_text,
        person_description=person_description,
        face_profile=face,
        preferences=prefs,
        wardrobe=wardrobe_list,
        place_image_bytes=place_bytes,
        selfie_b64=selfie_b64 or None,
    )

    recommendation = result.get("recommendation", {})
    recommendation_2 = result.get("recommendation_2", {})
    composite_image_url = result.get("composite_image_url", "")
    composite_image_url_2 = result.get("composite_image_url_2", "")
    place_analysis = result.get("place_analysis", "")
    budget = prefs.get("budget", "")
    gender = prefs.get("gender", "men").lower().replace("male", "men").replace("female", "women")

    # ── Collect all missing items from both outfits ────────────────────────────
    all_missing = recommendation.get("missing_items", []) + recommendation_2.get("missing_items", [])
    # Deduplicate by description
    seen_descs: set[str] = set()
    unique_missing = []
    for item in all_missing:
        key = f"{item.get('color','').lower()}-{item.get('category','').lower()}"
        if key not in seen_descs:
            seen_descs.add(key)
            unique_missing.append(item)

    # ── Get Gemini-powered specific product links ──────────────────────────────
    shopping_results = []
    if unique_missing:
        try:
            from app.services.shopping_aggregator import get_specific_product_links
            shopping_results = await get_specific_product_links(
                missing_items=unique_missing[:4],
                budget=budget,
                gender=gender,
                skin_tone=face.get("skin_tone", ""),
                situation=situation_text,
            )
        except Exception as e:
            logger.warning("Specific product links failed, falling back: %s", e)
            for item in unique_missing[:3]:
                results = await search_all_platforms(
                    category=item.get("category", "clothing"),
                    color=item.get("color", ""),
                    fit=prefs.get("fit", "Regular Fit"),
                    gender=gender,
                    description=item.get("description", ""),
                )
                for r in results:
                    r["for_item"] = item.get("description", "")
                shopping_results.extend(results)

    return {
        "recommendation": recommendation,
        "recommendation_2": recommendation_2,
        "composite_image_url": composite_image_url,
        "composite_image_url_2": composite_image_url_2,
        "place_analysis": place_analysis,
        "situation_text": situation_text,
        "person_description": person_description,
        "shopping_results": shopping_results,
        "wardrobe_count": len(wardrobe_list),
    }


@router.post("/generate-image")
async def generate_situation_image(
    outfit_description: str = Form(""),
    situation_text: str = Form(""),
    user_id: str = Form(""),
    face_profile: str = Form("{}"),
    place_image: Optional[UploadFile] = File(None),
):
    """Trigger image generation independently (lazy — user clicks a button)."""
    from app.services.situation_service import run_situation_pipeline
    try:
        face = json.loads(face_profile)
    except Exception:
        face = {}
    # Use situation_text as fallback if outfit_description is blank
    effective_desc = outfit_description.strip() or situation_text.strip() or "stylish outfit"
    place_bytes: Optional[bytes] = None
    if place_image and place_image.content_type and place_image.content_type.startswith("image/"):
        place_bytes = await place_image.read()

    # Minimal pipeline — only the image generation node
    from app.services.situation_service import generate_composite_image, SituationState
    state: SituationState = {
        "user_id": user_id or "anon",
        "situation_text": situation_text,
        "person_description": "",
        "face_profile": face,
        "preferences": {},
        "wardrobe": [],
        "place_image_bytes": place_bytes,
        "place_analysis": situation_text,
        "filtered_wardrobe": [],
        "recommendation": {"items": [{"description": effective_desc, "color": ""}]},
        "composite_image_url": "",
        "errors": [],
    }
    result = await generate_composite_image(state)
    return {"image_url": result.get("composite_image_url", "")}
