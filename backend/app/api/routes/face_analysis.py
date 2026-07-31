import uuid
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Form
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models import UserProfile
from app.services.face_analyzer import analyze_face
from app.services.cache_service import build_and_cache_profile_context
from app.schemas.user import FaceAnalysisResponse

router = APIRouter(prefix="/api/face", tags=["face-analysis"])


@router.post("/analyze", response_model=FaceAnalysisResponse)
async def analyze_face_endpoint(
    image: UploadFile = File(...),
    user_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Analyse uploaded selfie image.
    If user_id is provided (authenticated user), updates their existing profile.
    Otherwise creates a new anonymous profile.
    """
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    image_bytes = await image.read()
    if len(image_bytes) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large. Max 15MB.")

    # Analyse face (cached)
    features = await analyze_face(image_bytes)

    # Update existing user or create anonymous profile
    if user_id:
        try:
            result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
            user = result.scalar_one_or_none()
        except Exception:
            user = None

        if user:
            user.face_shape = features.get("face_shape")
            user.skin_tone = features.get("skin_tone")
            user.eye_color = features.get("eye_color")
            user.hair_color = features.get("hair_color")
            user.expression_vibe = features.get("expression_vibe")
            user.style_personality = features.get("style_personality")
            user.color_season = features.get("color_season")
            user.dominant_face_color_hex = features.get("dominant_face_color_hex")
            await db.commit()
            await db.refresh(user)
            return FaceAnalysisResponse(user_id=str(user.user_id), face_profile=features)

    # Create anonymous profile
    user = UserProfile(
        user_id=str(uuid.uuid4()),
        face_shape=features.get("face_shape"),
        skin_tone=features.get("skin_tone"),
        eye_color=features.get("eye_color"),
        hair_color=features.get("hair_color"),
        expression_vibe=features.get("expression_vibe"),
        style_personality=features.get("style_personality"),
        color_season=features.get("color_season"),
        dominant_face_color_hex=features.get("dominant_face_color_hex"),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return FaceAnalysisResponse(
        user_id=str(user.user_id),
        face_profile=features,
    )
