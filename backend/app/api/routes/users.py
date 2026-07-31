import uuid
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models import UserProfile
from app.schemas.user import (
    CreateProfileRequest, UpdatePreferencesRequest, UserProfileOut,
)
from app.services.cache_service import cache_delete, profile_context_key

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("/profile", response_model=UserProfileOut)
async def create_profile(body: CreateProfileRequest, db: AsyncSession = Depends(get_db)):
    user = UserProfile(
        user_id=str(uuid.uuid4()),
        gender=body.gender,
        fit_preference=body.fit,
        favorite_colors=body.favorite_colors or [],
        preferred_fabrics=body.fabrics or [],
        footwear_preference=body.footwear,
        priority_feature=body.priority,
        experiment_level=body.experiment_level or 3,
        budget=body.budget,
        sustainability=body.sustainability or False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _to_out(user)


@router.get("/profile/{user_id}", response_model=UserProfileOut)
async def get_profile(user_id: str, db: AsyncSession = Depends(get_db)):
    user = await _get_user(user_id, db)
    return _to_out(user)


@router.put("/profile/{user_id}/preferences", response_model=UserProfileOut)
async def update_preferences(
    user_id: str,
    body: UpdatePreferencesRequest,
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(user_id, db)

    if body.gender is not None:
        user.gender = body.gender
    if body.fit is not None:
        user.fit_preference = body.fit
    if body.favorite_colors is not None:
        user.favorite_colors = body.favorite_colors
    if body.fabrics is not None:
        user.preferred_fabrics = body.fabrics
    if body.footwear is not None:
        user.footwear_preference = body.footwear
    if body.priority is not None:
        user.priority_feature = body.priority
    if body.experiment_level is not None:
        user.experiment_level = body.experiment_level
    if body.budget is not None:
        user.budget = body.budget
    if body.sustainability is not None:
        user.sustainability = body.sustainability

    await db.commit()
    await db.refresh(user)

    # Invalidate profile context cache so Gemini gets fresh data on next request
    await cache_delete(profile_context_key(user_id))

    return _to_out(user)


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _get_user(user_id: str, db: AsyncSession) -> UserProfile:
    try:
        uuid.UUID(user_id)  # validate format
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")

    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _to_out(user: UserProfile) -> UserProfileOut:
    return UserProfileOut(
        user_id=str(user.user_id),
        gender=user.gender,
        face_shape=user.face_shape,
        skin_tone=user.skin_tone,
        expression_vibe=user.expression_vibe,
        style_personality=user.style_personality,
        color_season=user.color_season,
        fit_preference=user.fit_preference,
        favorite_colors=user.favorite_colors,
        preferred_fabrics=user.preferred_fabrics,
        experiment_level=user.experiment_level,
        priority_feature=user.priority_feature,
    )
