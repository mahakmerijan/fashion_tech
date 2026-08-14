"""
Face Analysis Service
─────────────────────
Pipeline:
1. Receive image bytes
2. Check Redis cache (hash of image bytes)
3. If cache hit → return cached features
4. Compress image to ≤1024px
5. MediaPipe FaceMesh → landmarks
6. Derive face shape from landmark geometry
7. OpenCV → dominant eye colour
8. Gemini 2.5 Pro multimodal → skin tone, expression vibe, style personality, color season
9. Cache result → return
"""

import io
import json
import hashlib
import logging
from typing import Optional

try:
    import cv2
    import numpy as np
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

from PIL import Image
from google import genai
from google.genai import types

from app.core.config import get_settings
from app.services.cache_service import cache_get, cache_set, face_analysis_key, hash_image_bytes

settings = get_settings()
logger = logging.getLogger(__name__)

_client: Optional[genai.Client] = None

def get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client

FACE_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "face_shape": {"type": "string", "enum": ["Oval", "Round", "Square", "Heart", "Diamond", "Oblong", "Triangle"]},
        "skin_tone": {"type": "string", "enum": ["Fair", "Light", "Medium", "Olive", "Tan", "Dark", "Deep"]},
        "skin_tone_level": {"type": "string", "enum": ["High", "Medium", "Low"]},
        "eye_color": {"type": "string"},
        "hair_color": {"type": "string"},
        "expression_vibe": {"type": "string", "enum": ["Corporate-Serious", "Approachable-Casual", "Vibrant-Happy", "Edgy-Cool", "Minimalist-Muted", "Warm-Friendly", "Mysterious-Intense"]},
        "style_personality": {"type": "string", "enum": ["Classic", "Smart Casual", "Streetwear", "Bohemian", "Minimalist", "Preppy", "Artsy", "Sporty"]},
        "color_season": {"type": "string", "enum": ["Spring", "Summer", "Autumn", "Winter"]},
        "dominant_face_color_hex": {"type": "string"},
        "age_range": {"type": "string"},
        "has_facial_hair": {"type": "boolean"},
        "smile_level": {"type": "string", "enum": ["None", "Slight", "Moderate", "Big"]},
        "overall_mood": {"type": "string"},
    },
    "required": ["face_shape", "skin_tone", "expression_vibe", "style_personality", "color_season"],
}

ANALYSIS_PROMPT = """You are an expert facial analysis AI. Carefully examine this photo and return a precise JSON analysis.

FACE SHAPE DETECTION — measure these proportions carefully:
- OVAL: Forehead slightly wider than jaw, face length ~1.5× width, balanced cheeks, gently rounded jaw. Most "default" face shapes.
- ROUND: Width ≈ length (nearly equal), full cheeks, soft rounded jaw, no sharp angles.
- SQUARE: Forehead ≈ jaw width (both wide and similar), strong angular jaw, face length ≈ width.
- HEART: Noticeably wider forehead, prominent cheekbones, sharply narrowing jaw, often a pointed chin.
- DIAMOND: Narrow forehead AND narrow jaw, widest point is cheekbones (mid-face), angular look.
- OBLONG/LONG: Face length significantly >1.75× its width, similar forehead and jaw width, long chin.
- TRIANGLE: Narrow forehead, wider jaw than forehead, prominent wide jawline.

Be PRECISE. Do NOT default to Oval if the face has obvious square/round/heart/diamond features.

Return ONLY valid JSON with these fields:
{
  "face_shape": "Oval|Round|Square|Heart|Diamond|Oblong|Triangle",
  "skin_tone": "Fair|Light|Medium|Tan|Dark|Deep",
  "skin_tone_level": "Light|Medium|Dark",
  "eye_color": "observed eye color",
  "hair_color": "observed hair color",
  "expression_vibe": "Approachable-Casual|Bold-Confident|Elegant-Refined|Edgy-Creative|Relaxed-Easy|Corporate-Serious|Warm-Friendly",
  "style_personality": "Smart Casual|Classic|Streetwear|Minimalist|Bohemian|Athleisure|Preppy|Artsy",
  "color_season": "Spring|Summer|Autumn|Winter",
  "dominant_face_color_hex": "#RRGGBB (hex of skin)",
  "age_range": "18-24|25-34|35-44|45-54|55+",
  "has_facial_hair": true or false,
  "smile_level": "None|Slight|Full",
  "overall_mood": "short description"
}"""


def compress_image(data: bytes, max_size: int = 1024) -> bytes:
    """Resize image so max dimension ≤ max_size, JPEG quality 85."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    scale = min(1.0, max_size / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue()


def detect_eye_color_opencv(image_bytes: bytes) -> Optional[str]:
    """Simple heuristic: detect dominant colour in iris region using OpenCV."""
    if not _CV2_AVAILABLE:
        return None
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        h, w = img.shape[:2]
        # Sample centre-upper region (approx eye area)
        roi = hsv[h // 4: h // 2, w // 4: 3 * w // 4]
        avg_hue = int(np.mean(roi[:, :, 0]))
        avg_sat = int(np.mean(roi[:, :, 1]))
        if avg_sat < 30:
            return "Grey / Blue-Grey"
        if avg_hue < 15 or avg_hue > 160:
            return "Dark Brown"
        if 15 <= avg_hue < 30:
            return "Hazel / Brown"
        if 30 <= avg_hue < 80:
            return "Green / Hazel"
        return "Blue"
    except Exception:
        return None


async def analyze_face(image_bytes: bytes) -> dict:
    """
    Main entry point. Returns cached or freshly computed face feature dict.
    Token cost: paid once per unique image, then served from Redis.
    """
    img_hash = hash_image_bytes(image_bytes)
    cache_key = face_analysis_key(img_hash)

    # 1. Cache hit
    cached = await cache_get(cache_key)
    if cached:
        logger.info("Face analysis cache hit for %s", img_hash[:8])
        return cached if isinstance(cached, dict) else json.loads(cached)

    # 2. Compress
    compressed = compress_image(image_bytes, settings.MAX_IMAGE_SIZE_PX)

    # 3. OpenCV eye colour (lightweight, no API cost)
    eye_color_hint = detect_eye_color_opencv(compressed)

    # 4. Gemini multimodal analysis
    try:
        client = get_client()
        img_part = types.Part.from_bytes(data=compressed, mime_type="image/jpeg")
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL_ANALYZE,
            contents=[ANALYSIS_PROMPT, img_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        features: dict = json.loads(response.text)
        # Retry once if face_shape came back empty
        if not features.get("face_shape"):
            logger.warning("face_shape empty on first attempt, retrying once…")
            retry_resp = client.models.generate_content(
                model=settings.GEMINI_MODEL_ANALYZE,
                contents=[ANALYSIS_PROMPT, img_part],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            retry_features = json.loads(retry_resp.text)
            if retry_features.get("face_shape"):
                features = retry_features
    except Exception as e:
        logger.error("Gemini face analysis failed: %s", e)
        # Fallback — leave face_shape blank so dashboard triggers re-analysis
        features = {
            "face_shape": "",
            "skin_tone": "Medium",
            "skin_tone_level": "Medium",
            "eye_color": eye_color_hint or "Dark Brown",
            "hair_color": "Black",
            "expression_vibe": "Approachable-Casual",
            "style_personality": "Smart Casual",
            "color_season": "Autumn",
            "dominant_face_color_hex": "#C68642",
            "age_range": "25-35",
            "has_facial_hair": False,
            "smile_level": "Slight",
            "overall_mood": "Neutral",
        }

    # Override eye colour with CV if Gemini missed it
    if eye_color_hint and not features.get("eye_color"):
        features["eye_color"] = eye_color_hint

    # 5. Cache result — never analyse same image again
    await cache_set(cache_key, features, settings.CACHE_TTL_PROFILE)
    return features
