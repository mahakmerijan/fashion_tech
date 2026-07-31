from pydantic import BaseModel, Field
from typing import Optional
import uuid


class FaceProfileOut(BaseModel):
    face_shape: str
    skin_tone: str
    skin_tone_level: Optional[str] = None
    eye_color: Optional[str] = None
    hair_color: Optional[str] = None
    expression_vibe: Optional[str] = "Approachable-Casual"
    style_personality: Optional[str] = "Smart Casual"
    color_season: Optional[str] = "Autumn"
    dominant_face_color_hex: Optional[str] = None
    age_range: Optional[str] = None
    has_facial_hair: Optional[bool] = None
    smile_level: Optional[str] = None
    overall_mood: Optional[str] = None


class FaceAnalysisResponse(BaseModel):
    user_id: str
    face_profile: FaceProfileOut


class CreateProfileRequest(BaseModel):
    gender: Optional[str] = None
    fit: Optional[str] = None
    favorite_colors: Optional[list[str]] = None
    fabrics: Optional[list[str]] = None
    footwear: Optional[str] = None
    priority: Optional[str] = None
    experiment_level: Optional[int] = Field(default=3, ge=1, le=5)
    budget: Optional[str] = None
    sustainability: Optional[bool] = False


class UpdatePreferencesRequest(BaseModel):
    gender: Optional[str] = None
    fit: Optional[str] = None
    favorite_colors: Optional[list[str]] = None
    fabrics: Optional[list[str]] = None
    footwear: Optional[str] = None
    priority: Optional[str] = None
    experiment_level: Optional[int] = Field(default=None, ge=1, le=5)
    budget: Optional[str] = None
    sustainability: Optional[bool] = None


class UserProfileOut(BaseModel):
    user_id: str
    gender: Optional[str]
    face_shape: Optional[str]
    skin_tone: Optional[str]
    expression_vibe: Optional[str]
    style_personality: Optional[str]
    color_season: Optional[str]
    fit_preference: Optional[str]
    favorite_colors: Optional[list[str]]
    preferred_fabrics: Optional[list[str]]
    experiment_level: Optional[int]
    priority_feature: Optional[str]

    class Config:
        from_attributes = True
