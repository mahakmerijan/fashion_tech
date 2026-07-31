import json
import hashlib
from typing import Any, Optional
import redis.asyncio as aioredis
from app.core.config import get_settings

settings = get_settings()

_pool: Optional[aioredis.ConnectionPool] = None


def get_redis_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=20,
            decode_responses=True,
        )
    return _pool


def get_redis() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=get_redis_pool())


# ─── Keys ────────────────────────────────────────────────────────────────────

def profile_context_key(user_id: str) -> str:
    return f"user:profile_context:{user_id}"


def retail_search_key(category: str, color: str, fit: str) -> str:
    norm = lambda s: s.lower().replace(" ", "-")
    return f"retail:search:{norm(category)}:{norm(color)}:{norm(fit)}"


def image_cache_key(prompt_hash: str) -> str:
    return f"img:cache:{prompt_hash}"


def face_analysis_key(image_hash: str) -> str:
    return f"face:analysis:{image_hash}"


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def cache_get(key: str) -> Optional[Any]:
    r = get_redis()
    val = await r.get(key)
    if val:
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return None


async def cache_set(key: str, value: Any, ttl: int) -> None:
    r = get_redis()
    await r.setex(key, ttl, json.dumps(value) if not isinstance(value, str) else value)


async def cache_delete(key: str) -> None:
    r = get_redis()
    await r.delete(key)


async def build_and_cache_profile_context(user_id: str, profile: dict, wardrobe: list[dict]) -> str:
    """Build the Markdown context string for Gemini and cache it in Redis."""
    lines = [
        "USER PROFILE:",
        f"- Gender: {profile.get('gender', 'N/A')}",
        f"- Face Shape: {profile.get('face_shape', 'N/A')} (Skin Tone: {profile.get('skin_tone', 'N/A')}, Vibe: {profile.get('expression_vibe', 'N/A')})",
        f"- Eye Color: {profile.get('eye_color', 'N/A')}, Hair: {profile.get('hair_color', 'N/A')}",
        f"- Color Season: {profile.get('color_season', 'N/A')}",
        f"- Style Preferences: {profile.get('fit_preference', 'Regular Fit')}, Fabrics: {', '.join(profile.get('preferred_fabrics') or [])}",
        f"- Favourite Colors: {', '.join(profile.get('favorite_colors') or [])}",
        f"- Priority: {profile.get('priority_feature', 'Style')}",
        f"- Experimentation Level: {profile.get('experiment_level', 3)}/5",
        "",
        "OWNED WARDROBE COLLECTION:",
    ]
    for i, item in enumerate(wardrobe, 1):
        lines.append(
            f"- Item_{i:03d}: {item.get('primary_color', '')} {item.get('detected_fabric', '')} "
            f"{item.get('estimated_fit', '')} {item.get('sub_category', item.get('category', 'Item'))} "
            f"[ID:{item.get('item_id', '')}]"
        )

    context = "\n".join(lines)
    key = profile_context_key(user_id)
    await cache_set(key, context, settings.CACHE_TTL_PROFILE)
    return context


def hash_image_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_outfit_prompt(user_face: dict, items: list[dict]) -> str:
    payload = json.dumps({"face": user_face, "items": sorted(items, key=lambda x: x.get("item_id", ""))}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()
