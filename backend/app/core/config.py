from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "StyleAI API"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://styleai:styleai@localhost:5432/styleai"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_TTL_PROFILE: int = 3600        # 1 hour
    CACHE_TTL_RETAIL: int = 43200        # 12 hours
    CACHE_TTL_IMAGE: int = 86400 * 7     # 7 days

    # Gemini
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL_RECOMMEND: str = "gemini-3.1-flash-lite"
    GEMINI_MODEL_ANALYZE: str = "gemini-3.1-flash-lite"
    # gemini-3.1-flash-lite-image is confirmed working; others are fallbacks
    GEMINI_MODEL_IMAGE: str = "gemini-3.1-flash-lite-image"
    GEMINI_MODEL_IMAGE_FALLBACKS: str = "gemini-3.1-flash-image,gemini-2.5-flash-image"

    # Object Storage (S3 / R2)
    S3_BUCKET: str = "styleai-media"
    S3_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    CDN_BASE_URL: str = ""

    # Vector DB (Qdrant)
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""

    # Shopping
    AMAZON_ACCESS_KEY: str = ""
    AMAZON_SECRET_KEY: str = ""
    AMAZON_PARTNER_TAG: str = ""
    FLIPKART_AFFILIATE_TOKEN: str = ""

    # Image sizing
    MAX_IMAGE_SIZE_PX: int = 1024

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
