"""
Situation-Based Outfit Recommendation Service
──────────────────────────────────────────────
LangGraph Pipeline:
  1. analyze_place   → Gemini Vision analyses the uploaded place image
  2. match_wardrobe  → Rule engine pre-filters wardrobe items
  3. gemini_reason   → Gemini 2.5 Pro: place + situation + wardrobe metadata → best outfit
  4. gen_image       → Gemini Image: user selfie + outfit + place → composite image
  5. format_result   → Structure final output

Key design:
- Place image sent to Gemini ONCE → analysis cached in Redis
- Wardrobe item images NOT re-sent (only cached metadata text)
- Composite image (user+outfit+place) cached by hash of (face+outfit+place)
"""

import io
import json
import hashlib
import logging
from typing import TypedDict, Optional, Annotated
import operator
import uuid

from PIL import Image
from google import genai
from google.genai import types as gtypes
from langgraph.graph import StateGraph, END

from app.core.config import get_settings
from app.services.cache_service import (
    cache_get, cache_set, build_and_cache_profile_context,
)
from app.services.image_generator import upload_image_to_storage

settings = get_settings()
logger = logging.getLogger(__name__)

_client: Optional[genai.Client] = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


# ─── State ────────────────────────────────────────────────────────────────────

class SituationState(TypedDict):
    user_id: str
    situation_text: str
    person_description: str
    face_profile: dict
    preferences: dict
    wardrobe: list[dict]
    place_image_bytes: Optional[bytes]
    selfie_b64: Optional[str]   # user's selfie as base64 for face-preserving image gen

    # Derived
    place_analysis: str
    filtered_wardrobe: list[dict]
    recommendation: dict
    composite_image_url: str
    errors: Annotated[list[str], operator.add]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def compress_image(data: bytes, max_size: int = 1024) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    scale = min(1.0, max_size / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ─── Nodes ────────────────────────────────────────────────────────────────────

async def analyze_place(state: SituationState) -> dict:
    """
    Analyse the uploaded place image with Gemini Vision.
    Result cached by image hash — not re-analysed on repeat requests.
    """
    place_bytes = state.get("place_image_bytes")
    if not place_bytes:
        return {"place_analysis": "No place image provided. Using text description only."}

    img_hash = hashlib.sha256(place_bytes).hexdigest()
    cache_key = f"place:analysis:{img_hash}"
    cached = await cache_get(cache_key)
    if cached:
        return {"place_analysis": cached}

    compressed = compress_image(place_bytes)
    try:
        client = get_client()
        prompt = (
            "Analyse this place/venue image and describe in 3-5 sentences: "
            "the type of venue, lighting, colour palette, formality level, "
            "dress code expectations, and overall atmosphere. "
            "Be specific and focus on fashion-relevant details."
        )
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL_ANALYZE,
            contents=[prompt, gtypes.Part.from_bytes(data=compressed, mime_type="image/jpeg")],
            config=gtypes.GenerateContentConfig(temperature=0.1, max_output_tokens=512),
        )
        analysis = response.text.strip()
    except Exception as e:
        logger.error("Place analysis failed: %s", e)
        analysis = "Unable to analyse place image. Proceeding with text description."

    await cache_set(cache_key, analysis, settings.CACHE_TTL_PROFILE)
    return {"place_analysis": analysis}


def match_wardrobe(state: SituationState) -> dict:
    """Rule-based pre-filter — no LLM cost."""
    wardrobe = state.get("wardrobe", [])
    situation = (state.get("situation_text", "") + " " + state.get("place_analysis", "")).lower()

    formality_floor = 0.0
    formality_ceil = 1.0

    if any(k in situation for k in ["interview", "corporate", "formal", "business", "investor", "office"]):
        formality_floor = 0.6
    elif any(k in situation for k in ["wedding", "gala", "ceremony", "award"]):
        formality_floor = 0.7
    elif any(k in situation for k in ["casual", "college", "beach", "travel", "chill", "park"]):
        formality_ceil = 0.55
    elif any(k in situation for k in ["party", "club", "rooftop", "birthday"]):
        formality_floor, formality_ceil = 0.35, 0.8

    filtered = [
        i for i in wardrobe
        if formality_floor <= float(i.get("formality_score", 0.3)) <= formality_ceil
    ] or wardrobe  # fallback: use all if nothing passes

    return {"filtered_wardrobe": filtered}


async def gemini_reason(state: SituationState) -> dict:
    """
    Core reasoning: Gemini 2.5 Pro picks the best outfit from the wardrobe
    given the place, situation, and user profile.
    Wardrobe items sent as TEXT metadata only — no image re-uploads.
    """
    face = state.get("face_profile", {})
    prefs = state.get("preferences", {})
    filtered = state.get("filtered_wardrobe", [])
    situation = state.get("situation_text", "")
    person = state.get("person_description", "")
    place_analysis = state.get("place_analysis", "")

    gender_raw = (prefs.get("gender") or "").lower()
    if gender_raw in ("female", "woman"):
        gender_label = "Female"
    elif gender_raw in ("others", "non-binary"):
        gender_label = "Non-binary"
    else:
        gender_label = "Male"

    # Build compact wardrobe text
    wardrobe_text = "\n".join(
        f"  [ID:{i.get('item_id','?')}] {i.get('primary_color','')} "
        f"{i.get('sub_category') or i.get('category','Item')} | "
        f"fit:{i.get('estimated_fit','')} | fabric:{i.get('detected_fabric','')} | "
        f"formality:{i.get('formality_score',0.3):.1f} | vibe:{i.get('style_vibe','')}"
        for i in filtered[:25]
    )

    prompt = f"""You are an expert personal stylist AI. Select the best outfit for this specific situation.

PERSON PROFILE:
- Gender: {gender_label}
- Face: {face.get('face_shape','?')} face, {face.get('skin_tone','?')} skin, {face.get('color_season','?')} colour season
- Preferred style: {prefs.get('style_personality') or face.get('style_personality','Smart Casual')}
- Preferred fit: {prefs.get('fit','Regular Fit')}
- Favourite colours: {', '.join(prefs.get('favorite_colors') or prefs.get('favourite_colors') or [])}
- Experimentation: {prefs.get('experiment_level',3)}/5

SITUATION:
{situation}

PERSON BEING MET:
{person or 'Not specified'}

PLACE/VENUE ANALYSIS:
{place_analysis}

AVAILABLE WARDROBE ITEMS:
{wardrobe_text or 'No wardrobe items uploaded.'}

TASK:
1. Pick the single best outfit combination from the wardrobe (max 4-5 pieces)
2. Identify any missing items that would complete the look
3. Explain WHY this outfit works for this specific situation and place
4. Give 3-5 styling tips
5. Suggest colour pairings

Return ONLY valid JSON:
{{
  "outfit_id": "unique_id",
  "title": "Outfit Name",
  "rationale": "Detailed explanation of why this outfit works for the situation",
  "items": [
    {{
      "item_id": "ID_from_wardrobe",
      "category": "Shirt",
      "description": "exact description",
      "color": "color",
      "from_wardrobe": true
    }}
  ],
  "missing_items": [
    {{
      "item_id": null,
      "category": "category",
      "description": "what to buy",
      "color": "color",
      "from_wardrobe": false,
      "search_query": "search term for shopping sites"
    }}
  ],
  "styling_tips": ["tip1", "tip2", "tip3"],
  "color_suggestions": ["suggestion1"],
  "place_outfit_compatibility": "High|Medium|Low",
  "confidence": 0.95
}}"""

    try:
        client = get_client()
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL_RECOMMEND,
            contents=prompt,
            config=gtypes.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.25,
                max_output_tokens=2048,
            ),
        )
        rec = json.loads(response.text)
        if not rec.get("outfit_id"):
            rec["outfit_id"] = str(uuid.uuid4())
        return {"recommendation": rec}
    except Exception as e:
        logger.error("Gemini outfit reasoning failed: %s", e)
        return {"recommendation": {}, "errors": [f"Reasoning error: {e}"]}


async def generate_composite_image(state: SituationState) -> dict:
    """
    Generate an image of the user wearing the recommended outfit AT the specific place.
    Uses both the user's face profile description and the place image.
    Cached by hash(face + outfit + place_image_hash).
    """
    rec = state.get("recommendation", {})
    if not rec or not rec.get("items"):
        return {"composite_image_url": ""}

    face = state.get("face_profile", {})
    place_bytes = state.get("place_image_bytes")
    items = rec.get("items", [])
    prefs = state.get("preferences", {})
    selfie_b64 = state.get("selfie_b64")

    # Decode selfie if available
    selfie_bytes: Optional[bytes] = None
    if selfie_b64:
        import base64 as _b64
        try:
            raw = selfie_b64.split(",", 1)[-1]
            selfie_bytes = _b64.b64decode(raw)
        except Exception:
            selfie_bytes = None

    # Resolve gender word
    gender_raw = (prefs.get("gender") or "").lower()
    if gender_raw in ("female", "woman"):
        subject = "woman"
    elif gender_raw in ("others", "non-binary"):
        subject = "person"
    else:
        subject = "man"

    # Build cache key
    outfit_desc = "|".join(f"{i.get('color','')}_{i.get('description','')}" for i in items)
    place_hash = hashlib.sha256(place_bytes).hexdigest()[:16] if place_bytes else "noplace"
    face_key = f"{face.get('face_shape','')}{face.get('skin_tone','')}{face.get('hair_color','')}"
    cache_hash = hashlib.sha256(f"{face_key}|{outfit_desc}|{place_hash}".encode()).hexdigest()
    cache_key = f"img:situation:{cache_hash}"

    cached = await cache_get(cache_key)
    if cached:
        logger.info("Composite image cache HIT %s", cache_hash[:8])
        return {"composite_image_url": cached}

    # Build Gemini image prompt
    outfit_parts = [f"{i.get('color','')} {i.get('description','')}" for i in items[:4]]
    outfit_str = ", ".join(outfit_parts)
    style_hint = prefs.get("style_personality", "")
    style_phrase = f" The overall aesthetic is {style_hint}." if style_hint else ""

    face_desc = (
        f"{subject} with {face.get('face_shape', 'oval')} face shape, "
        f"{face.get('skin_tone', 'medium')} skin tone, "
        f"{face.get('hair_color', 'dark')} hair"
    )

    if place_bytes:
        place_compressed = compress_image(place_bytes)
        if selfie_bytes:
            image_prompt = (
                f"Professional fashion editorial photograph of a {subject}. "
                f"Match the face, skin tone, and hair from the FIRST reference photo (the selfie). "
                f"CRITICAL PROPORTIONS: Render with natural human anatomy — face size proportional to the body, "
                f"correct head-to-body ratio (~1:7), realistic full-body scale. "
                f"The image must look like a single seamless photograph, not a composite. "
                f"This is a {subject} — do NOT change the gender or skin tone. "
                f"Outfit: {outfit_str}.{style_phrase} "
                f"Place the person naturally inside the venue shown in the SECOND photo. "
                f"Full body shot, realistic lighting matching the venue, high quality fashion photography."
            )
        else:
            image_prompt = (
                f"Fashion editorial photograph of a {subject}. "
                f"IMPORTANT: This is a {subject} — do NOT change the gender. "
                f"Natural human proportions, correct head-to-body ratio, realistic anatomy. "
                f"A {face_desc} wearing {outfit_str}.{style_phrase} "
                f"Standing naturally in the venue shown in this image. "
                f"Full body shot, natural lighting, high quality fashion photography."
            )
    else:
        place_analysis = state.get("place_analysis", "")
        if selfie_bytes:
            image_prompt = (
                f"Professional fashion editorial photograph of a {subject}. "
                f"Match the face, skin tone, and hair from the reference photo (the selfie). "
                f"CRITICAL PROPORTIONS: Render with natural human anatomy — face size proportional to the body, "
                f"correct head-to-body ratio (~1:7). Must look like a single seamless photograph, not a composite. "
                f"This is a {subject} — do NOT change the gender or skin tone. "
                f"Outfit: {outfit_str}.{style_phrase} "
                f"Setting: {state.get('situation_text', 'casual outdoor')}. "
                f"Full body shot, professional fashion photography, well-lit, natural proportions."
            )
        else:
            image_prompt = (
                f"Fashion editorial photograph of a {subject}. "
                f"IMPORTANT: This is a {subject} — do NOT change the gender. "
                f"Natural human proportions, correct head-to-body ratio, realistic anatomy. "
                f"A {face_desc} wearing {outfit_str}.{style_phrase} "
                f"Setting: {state.get('situation_text', 'casual outdoor')}. "
                f"Venue ambiance: {place_analysis[:200]}. "
                f"Full body shot, realistic fashion photography, natural proportions."
            )

    try:
        client = get_client()

        # Build contents list with optional selfie and/or place image
        contents: list = [image_prompt]
        if selfie_bytes:
            contents.append(gtypes.Part.from_bytes(data=compress_image(selfie_bytes), mime_type="image/jpeg"))
        if place_bytes:
            contents.append(gtypes.Part.from_bytes(data=place_compressed, mime_type="image/jpeg"))

        if len(contents) > 1:
            # Multimodal: at least one image input
            response = client.models.generate_content(
                model=settings.GEMINI_MODEL_IMAGE,
                contents=contents,
                config=gtypes.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                ),
            )
            image_data: Optional[bytes] = None
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image"):
                    image_data = part.inline_data.data
                    break
        else:
            # Imagen 4: text-only → image
            img_response = client.models.generate_images(
                model="imagen-4.0-generate-001",
                prompt=image_prompt,
                config=gtypes.GenerateImagesConfig(number_of_images=1),
            )
            image_data = img_response.generated_images[0].image.image_bytes if img_response.generated_images else None

        if not image_data:
            raise ValueError("No image returned from model")

        image_url = await upload_image_to_storage(
            image_data, f"generated/{state['user_id']}/situation_{cache_hash[:12]}.jpg"
        )
        await cache_set(cache_key, image_url, settings.CACHE_TTL_IMAGE)
        return {"composite_image_url": image_url}

    except Exception as e:
        err_str = str(e)
        logger.error("Composite image generation failed: %s", err_str)

        # For quota/billing issues, generate a styled preview card URL via SVG
        is_quota = "429" in err_str or "quota" in err_str.lower() or "billing" in err_str.lower()
        is_unavail = "404" in err_str or "NOT_FOUND" in err_str

        if is_quota or is_unavail:
            # Return a special marker — frontend will render a style card
            outfit_items = state.get("recommendation", {}).get("items", [])
            outfit_summary = "; ".join(
                f"{i.get('color','')} {i.get('description','')}" for i in outfit_items[:3]
            )
            card_url = (
                f"__STYLE_CARD__|{outfit_summary}|"
                f"{state.get('situation_text','')[:80]}"
            )
            return {"composite_image_url": card_url}

        placeholder = "https://placehold.co/512x768/7c3aed/white?text=Image+Generation+Failed"
        return {"composite_image_url": placeholder}


# ─── Graph ────────────────────────────────────────────────────────────────────

def build_situation_graph():
    graph = StateGraph(SituationState)
    graph.add_node("analyze_place", analyze_place)
    graph.add_node("match_wardrobe", match_wardrobe)
    graph.add_node("gemini_reason", gemini_reason)
    graph.add_node("generate_composite_image", generate_composite_image)

    graph.set_entry_point("analyze_place")
    graph.add_edge("analyze_place", "match_wardrobe")
    graph.add_edge("match_wardrobe", "gemini_reason")
    graph.add_edge("gemini_reason", "generate_composite_image")
    graph.add_edge("generate_composite_image", END)

    return graph.compile()


_situation_graph = None


def get_situation_graph():
    global _situation_graph
    if _situation_graph is None:
        _situation_graph = build_situation_graph()
    return _situation_graph


async def run_situation_pipeline(
    user_id: str,
    situation_text: str,
    person_description: str,
    face_profile: dict,
    preferences: dict,
    wardrobe: list[dict],
    place_image_bytes: Optional[bytes] = None,
    selfie_b64: Optional[str] = None,
) -> dict:
    graph = get_situation_graph()
    result = await graph.ainvoke({
        "user_id": user_id,
        "situation_text": situation_text,
        "person_description": person_description,
        "face_profile": face_profile,
        "preferences": preferences,
        "wardrobe": wardrobe,
        "place_image_bytes": place_image_bytes,
        "selfie_b64": selfie_b64,
        "place_analysis": "",
        "filtered_wardrobe": [],
        "recommendation": {},
        "composite_image_url": "",
        "errors": [],
    })
    return result
