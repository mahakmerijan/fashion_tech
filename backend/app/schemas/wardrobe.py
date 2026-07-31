from pydantic import BaseModel
from typing import Optional
import uuid


class WardrobeItemOut(BaseModel):
    item_id: str
    image_url: str
    category: str
    sub_category: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_colors: Optional[list[str]] = None
    pattern: Optional[str] = None
    estimated_fit: Optional[str] = None
    detected_fabric: Optional[str] = None
    formality_score: Optional[float] = None
    style_vibe: Optional[str] = None
    ai_metadata: Optional[dict] = None

    class Config:
        from_attributes = True


class WardrobeUploadResponse(BaseModel):
    items: list[WardrobeItemOut]
    total_uploaded: int


class WardrobeListResponse(BaseModel):
    items: list[WardrobeItemOut]
    total: int
