import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, Boolean, Float, ForeignKey, DateTime, Enum as SAEnum, JSON, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.config import get_settings
import enum
from app.db.database import Base

_settings = get_settings()
_using_pg = not _settings.DATABASE_URL.startswith("sqlite")

if _using_pg:
    from sqlalchemy.dialects.postgresql import UUID as _PG_UUID, JSONB, ARRAY as _PG_ARRAY
    _UUID_type = _PG_UUID(as_uuid=True)
    _JSONB_type = JSONB
    def _ARRAY(item_type): return _PG_ARRAY(item_type)
else:
    # SQLite-compatible alternatives
    _UUID_type = String(36)
    _JSONB_type = JSON
    def _ARRAY(item_type): return JSON  # store arrays as JSON


class GenderEnum(str, enum.Enum):
    male = "Male"
    female = "Female"
    others = "Others"


class FitEnum(str, enum.Enum):
    slim = "Slim Fit"
    regular = "Regular Fit"
    loose = "Loose Fit"
    oversized = "Oversized"


class PriorityEnum(str, enum.Enum):
    style = "Style"
    comfort = "Comfort"
    durability = "Durability"
    versatility = "Versatility"


def utcnow():
    return datetime.now(timezone.utc)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(_UUID_type, primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Auth
    email: Mapped[str] = mapped_column(String(255), nullable=True, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=True)

    gender: Mapped[str] = mapped_column(String(20), nullable=True)

    # AI-extracted face traits
    face_shape: Mapped[str] = mapped_column(String(30), nullable=True)
    skin_tone: Mapped[str] = mapped_column(String(30), nullable=True)
    eye_color: Mapped[str] = mapped_column(String(30), nullable=True)
    hair_color: Mapped[str] = mapped_column(String(30), nullable=True)
    expression_vibe: Mapped[str] = mapped_column(String(60), nullable=True)
    style_personality: Mapped[str] = mapped_column(String(60), nullable=True)
    color_season: Mapped[str] = mapped_column(String(30), nullable=True)
    dominant_face_color_hex: Mapped[str] = mapped_column(String(7), nullable=True)
    selfie_url: Mapped[str] = mapped_column(Text, nullable=True)

    # Preferences
    fit_preference: Mapped[str] = mapped_column(String(30), nullable=True)
    preferred_fabrics: Mapped[list] = mapped_column(_ARRAY(Text), nullable=True, default=list)
    favorite_colors: Mapped[list] = mapped_column(_ARRAY(Text), nullable=True, default=list)
    footwear_preference: Mapped[str] = mapped_column(String(40), nullable=True)
    priority_feature: Mapped[str] = mapped_column(String(30), nullable=True)
    experiment_level: Mapped[int] = mapped_column(Integer, default=3)
    budget: Mapped[str] = mapped_column(String(30), nullable=True)
    style_vibe: Mapped[str] = mapped_column(String(60), nullable=True)
    sustainability: Mapped[bool] = mapped_column(Boolean, default=False)

    wardrobe_items: Mapped[list["WardrobeItem"]] = relationship("WardrobeItem", back_populates="user", cascade="all, delete-orphan")
    generated_images: Mapped[list["GeneratedImage"]] = relationship("GeneratedImage", back_populates="user", cascade="all, delete-orphan")


class WardrobeItem(Base):
    __tablename__ = "wardrobe_items"

    item_id: Mapped[str] = mapped_column(_UUID_type, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(_UUID_type, ForeignKey("user_profiles.user_id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="Unknown")
    sub_category: Mapped[str] = mapped_column(String(60), nullable=True)
    primary_color: Mapped[str] = mapped_column(String(30), nullable=True)
    secondary_colors: Mapped[list] = mapped_column(_ARRAY(Text), nullable=True, default=list)
    pattern: Mapped[str] = mapped_column(String(40), default="Solid")
    estimated_fit: Mapped[str] = mapped_column(String(30), nullable=True)
    material_weight: Mapped[str] = mapped_column(String(20), nullable=True)
    detected_fabric: Mapped[str] = mapped_column(String(40), nullable=True)
    formality_score: Mapped[float] = mapped_column(Float, nullable=True)
    brand: Mapped[str] = mapped_column(String(60), nullable=True)
    ai_metadata: Mapped[dict] = mapped_column(_JSONB_type, default=dict)
    embedding: Mapped[list] = mapped_column(_ARRAY(Float), nullable=True)

    user: Mapped["UserProfile"] = relationship("UserProfile", back_populates="wardrobe_items")


class GeneratedImage(Base):
    __tablename__ = "generated_images"

    id: Mapped[str] = mapped_column(_UUID_type, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(_UUID_type, ForeignKey("user_profiles.user_id", ondelete="CASCADE"))
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    outfit_description: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["UserProfile"] = relationship("UserProfile", back_populates="generated_images")
