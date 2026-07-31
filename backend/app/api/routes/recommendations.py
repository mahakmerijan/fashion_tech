import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models import UserProfile, WardrobeItem
from app.services.recommendation_engine import get_recommendations
from app.schemas.recommendations import (
    RecommendationRequest, RecommendationResponse, OutfitRecommendation,
    ImageGenerationRequest, ImageGenerationResponse,
    ShoppingResponse, ShoppingResult,
)
from app.services.image_generator import generate_outfit_image
from app.services.shopping_aggregator import search_all_platforms

router = APIRouter(prefix="/api", tags=["recommendations"])


@router.post("/recommendations", response_model=RecommendationResponse)
async def recommendations_endpoint(
    body: RecommendationRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Get AI-powered outfit recommendations.
    Uses LangGraph pipeline with Redis-cached profile context.
    Token cost: ~95% cheaper than raw image analysis.
    """
    user_id = body.userId or str(uuid.uuid4())
    profile: dict = {}
    wardrobe: list[dict] = []

    # Load profile from DB if user_id provided
    if body.userId:
        try:
            uid = uuid.UUID(body.userId)
            result = await db.execute(select(UserProfile).where(UserProfile.user_id == uid))
            user = result.scalar_one_or_none()
            if user:
                profile = {
                    "user_id": str(user.user_id),
                    "gender": user.gender,
                    "face_shape": user.face_shape,
                    "skin_tone": user.skin_tone,
                    "eye_color": user.eye_color,
                    "hair_color": user.hair_color,
                    "expression_vibe": user.expression_vibe,
                    "style_personality": user.style_personality,
                    "color_season": user.color_season,
                    "fit_preference": user.fit_preference,
                    "preferred_fabrics": user.preferred_fabrics or [],
                    "favorite_colors": user.favorite_colors or [],
                    "footwear_preference": user.footwear_preference,
                    "priority_feature": user.priority_feature,
                    "experiment_level": user.experiment_level,
                }
                # Load wardrobe
                w_result = await db.execute(
                    select(WardrobeItem).where(WardrobeItem.user_id == uid)
                )
                wardrobe_items = w_result.scalars().all()
                wardrobe = [
                    {
                        "item_id": str(i.item_id),
                        "category": i.category,
                        "sub_category": i.sub_category,
                        "primary_color": i.primary_color,
                        "estimated_fit": i.estimated_fit,
                        "detected_fabric": i.detected_fabric,
                        "formality_score": i.formality_score or 0.3,
                        "style_vibe": i.ai_metadata.get("style_vibe") if i.ai_metadata else None,
                    }
                    for i in wardrobe_items
                ]
        except (ValueError, Exception):
            pass

    # Merge any client-side profile/preferences passed directly
    if body.faceProfile:
        profile.update(body.faceProfile)
    if body.preferences:
        profile.update(body.preferences)

    # Run LangGraph recommendation pipeline
    recs = await get_recommendations(
        user_id=user_id,
        occasion=body.occasion,
        profile=profile,
        wardrobe=wardrobe,
    )

    return RecommendationResponse(
        recommendations=[OutfitRecommendation(**r) for r in recs],
        occasion=body.occasion,
        total=len(recs),
    )


@router.post("/images/generate", response_model=ImageGenerationResponse)
async def generate_image_endpoint(
    body: ImageGenerationRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate virtual try-on image using Gemini Image.
    Implements hash-based caching — same outfit never generated twice.
    """
    result = await generate_outfit_image(
        user_id=body.user_id,
        outfit_id=body.outfit_id,
        face_profile=body.face_profile or {},
        outfit_items=[i.model_dump() for i in body.items],
        occasion=body.occasion or "Casual",
        selfie_b64=body.selfie_b64,
        place_b64=body.place_b64,
        user_feedback=body.user_feedback,
        db_session=db,
    )
    return ImageGenerationResponse(**result)


@router.get("/shopping/search", response_model=ShoppingResponse)
async def shopping_search(category: str, color: str, fit: str, gender: str = "men"):
    """
    Search shopping platforms (Amazon, Flipkart, Myntra, AJIO).
    Results are Redis-cached for 12 hours.
    """
    from app.services.cache_service import retail_search_key, cache_get
    cache_key = retail_search_key(category, color, fit)
    was_cached = bool(await cache_get(cache_key))

    results = await search_all_platforms(category, color, fit, gender)
    query = f"{gender} {color} {fit} {category}"

    return ShoppingResponse(
        results=[ShoppingResult(**r) for r in results],
        query=query,
        cached=was_cached,
    )
