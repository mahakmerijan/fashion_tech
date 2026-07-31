from pydantic import BaseModel
from typing import Optional


class OutfitItem(BaseModel):
    item_id: Optional[str] = None
    category: str
    description: str
    color: str
    from_wardrobe: bool = True
    image_url: Optional[str] = None


class OutfitRecommendation(BaseModel):
    outfit_id: str
    title: str
    rationale: Optional[str] = None
    items: list[OutfitItem]
    missing_items: list[OutfitItem] = []
    styling_tips: list[str] = []
    color_suggestions: list[str] = []
    formality_match: Optional[str] = None
    generated_image_url: Optional[str] = None


class RecommendationRequest(BaseModel):
    userId: Optional[str] = None
    occasion: str
    faceProfile: Optional[dict] = None
    preferences: Optional[dict] = None
    wardrobeIds: Optional[list[str]] = None


class RecommendationResponse(BaseModel):
    recommendations: list[OutfitRecommendation]
    occasion: str
    total: int


class ImageGenerationRequest(BaseModel):
    outfit_id: str
    user_id: str
    items: list[OutfitItem]
    face_profile: Optional[dict] = None
    occasion: Optional[str] = "Casual"
    selfie_b64: Optional[str] = None    # base64 data-URL of user's selfie
    place_b64: Optional[str] = None     # base64 data-URL of the venue/place image
    user_feedback: Optional[str] = None # free-text tweak from the user


class ImageGenerationResponse(BaseModel):
    image_url: str
    from_cache: bool
    prompt_hash: str


class ShoppingResult(BaseModel):
    name: str
    price: str
    url: str
    image_url: str
    platform: str


class ShoppingResponse(BaseModel):
    results: list[ShoppingResult]
    query: str
    cached: bool
