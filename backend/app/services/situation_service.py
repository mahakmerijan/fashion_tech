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
    recommendation_2: dict          # second outfit option
    composite_image_url: str
    composite_image_url_2: str      # second outfit image
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
Create TWO distinct outfit options from the wardrobe that suit this situation and venue.
For each outfit:
1. Pick 3-5 pieces from the wardrobe — the two outfits must use DIFFERENT combinations
2. Identify TRULY MISSING items NOT in the wardrobe (different colours/categories not present). Do NOT list wardrobe items as missing.
3. Match the outfit to the venue ambiance, formality, and user's skin tone/colour season
4. Give styling tips specific to the venue lighting and setting
5. Suggest complementary colours

Return ONLY valid JSON with this exact structure:
{{
  "outfit_1": {{
    "outfit_id": "unique_id_1",
    "title": "First Outfit Name",
    "rationale": "Why this works for the situation, venue, and skin tone",
    "items": [{{"item_id": "ID", "category": "Shirt", "description": "desc", "color": "color", "from_wardrobe": true}}],
    "missing_items": [{{"item_id": null, "category": "cat", "description": "what to buy", "color": "color", "from_wardrobe": false}}],
    "styling_tips": ["tip1", "tip2"],
    "color_suggestions": ["suggestion1"],
    "place_outfit_compatibility": "High|Medium|Low",
    "confidence": 0.92
  }},
  "outfit_2": {{
    "outfit_id": "unique_id_2",
    "title": "Second Outfit Name (different style/pieces from outfit_1)",
    "rationale": "Why this alternative works",
    "items": [{{"item_id": "ID", "category": "Pants", "description": "desc", "color": "color", "from_wardrobe": true}}],
    "missing_items": [{{"item_id": null, "category": "cat", "description": "what to buy", "color": "color", "from_wardrobe": false}}],
    "styling_tips": ["tip1", "tip2"],
    "color_suggestions": ["suggestion1"],
    "place_outfit_compatibility": "High|Medium|Low",
    "confidence": 0.88
  }}
}}"""

    try:
        client = get_client()
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL_RECOMMEND,
            contents=prompt,
            config=gtypes.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3,
                max_output_tokens=3000,
            ),
        )
        data = json.loads(response.text)
        rec1 = data.get("outfit_1", {})
        rec2 = data.get("outfit_2", {})
        if not rec1.get("outfit_id"):
            rec1["outfit_id"] = str(uuid.uuid4())
        if not rec2.get("outfit_id"):
            rec2["outfit_id"] = str(uuid.uuid4())
        # Also expose the primary recommendation as rec for backward-compat
        return {"recommendation": rec1, "recommendation_2": rec2}
    except Exception as e:
        logger.error("Gemini outfit reasoning failed: %s", e)
        return {"recommendation": {}, "recommendation_2": {}, "errors": [f"Reasoning error: {e}"]}


async def generate_composite_image(state: SituationState) -> dict:
    """
    Generate an image of the user wearing the recommended outfit AT the specific place.
    Dynamically includes:
    - User selfie (face reference)
    - Actual wardrobe dress images for each recommended item
    - Place/venue image
    - User's style preferences, colour season, favourite colours in prompt
    Cached by hash(face + outfit + place + dress_count).
    """
    import asyncio as _asyncio
    import base64 as _b64
    import httpx

    rec = state.get("recommendation", {})
    if not rec or not rec.get("items"):
        return {"composite_image_url": ""}

    face = state.get("face_profile", {})
    place_bytes = state.get("place_image_bytes")
    items = rec.get("items", [])
    prefs = state.get("preferences", {})
    selfie_b64 = state.get("selfie_b64")
    wardrobe = state.get("wardrobe", [])

    # Map item_id → image_url from full wardrobe list
    wardrobe_image_map = {str(w.get("item_id", "")): w.get("image_url", "") for w in wardrobe}

    # Decode selfie
    selfie_bytes: Optional[bytes] = None
    if selfie_b64:
        try:
            selfie_bytes = _b64.b64decode(selfie_b64.split(",", 1)[-1])
        except Exception:
            pass

    # Fetch actual dress images from wardrobe URLs concurrently
    async def _fetch(url: str) -> Optional[bytes]:
        if not url or not url.startswith("http") or "localhost" in url or "127.0.0" in url:
            return None
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(url, headers={"User-Agent": "StyleAI/1.0"})
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                    return r.content
        except Exception:
            pass
        return None

    dress_urls = [wardrobe_image_map.get(str(i.get("item_id", "")), "") for i in items[:4]]
    fetched_raw = await _asyncio.gather(*[_fetch(u) for u in dress_urls], return_exceptions=True)
    dress_images = [compress_image(f, 512) for f in fetched_raw if isinstance(f, bytes) and len(f) > 1000]

    # Resolve gender
    gender_raw = (prefs.get("gender") or "").lower()
    subject = "woman" if gender_raw in ("female", "woman") else ("person" if gender_raw in ("others", "non-binary") else "man")

    # Style context from user profile
    style_hint = prefs.get("style_personality") or face.get("style_personality", "")
    color_season = face.get("color_season", "")
    fav_colors = ", ".join(prefs.get("favorite_colors") or prefs.get("favourite_colors") or [])
    style_ctx = " | ".join(filter(None, [
        f"Style: {style_hint}" if style_hint else "",
        f"Colour season: {color_season}" if color_season else "",
        f"Favourite colours: {fav_colors}" if fav_colors else "",
    ]))

    # Build cache key (includes dress count so new images invalidate old cache)
    outfit_desc = "|".join(f"{i.get('color','')}_{i.get('description','')}" for i in items)
    place_hash = hashlib.sha256(place_bytes).hexdigest()[:16] if place_bytes else "noplace"
    face_key = f"{face.get('face_shape','')}{face.get('skin_tone','')}{face.get('hair_color','')}"
    cache_hash = hashlib.sha256(
        f"{face_key}|{outfit_desc}|{place_hash}|dress{len(dress_images)}".encode()
    ).hexdigest()
    cache_key = f"img:situation:{cache_hash}"

    cached = await cache_get(cache_key)
    if cached:
        logger.info("Composite image cache HIT %s", cache_hash[:8])
        return {"composite_image_url": cached}

    # Build outfit text description
    outfit_parts = [f"{i.get('color','')} {i.get('description','')}" for i in items[:4]]
    outfit_str = ", ".join(p for p in outfit_parts if p)
    face_desc = (
        f"{subject} with {face.get('face_shape') or 'defined'} face shape, "
        f"{face.get('skin_tone', 'medium')} skin tone, "
        f"{face.get('hair_color', 'dark')} hair"
    )

    has_selfie = selfie_bytes is not None
    has_dresses = len(dress_images) > 0
    has_place = place_bytes is not None

    # Build reference image instructions
    ref_lines = []
    img_idx = 1
    if has_selfie:
        ref_lines.append(f"IMAGE {img_idx} (selfie): match this person's exact face, skin tone, and hair — do NOT alter gender or appearance")
        img_idx += 1
    for n in range(len(dress_images)):
        ref_lines.append(f"IMAGE {img_idx + n}: actual clothing item {n+1} — reproduce this garment accurately on the person's body")
    img_idx += len(dress_images)
    if has_place:
        ref_lines.append(f"IMAGE {img_idx} (venue): place the person naturally inside this setting with matching lighting")

    ref_block = "\n".join(f"- {r}" for r in ref_lines) if ref_lines else ""

    if ref_lines:
        image_prompt = (
            f"Professional fashion editorial photograph of a {subject}.\n"
            f"Reference images:\n{ref_block}\n\n"
            f"RULES:\n"
            f"- Preserve exact person from selfie (face, skin, hair)\n"
            f"- Render the actual clothing from dress reference images on the person\n"
            f"- Natural anatomy: head-to-body ratio ~1:7, realistic full-body proportions\n"
            f"- Single seamless editorial photograph — NOT a collage or composite\n"
            f"Outfit: {outfit_str}\n"
            f"User style context: {style_ctx}\n"
            f"Occasion: {state.get('situation_text', 'casual')}\n"
            f"Full body shot, professional fashion photography, sharp details."
        )
    else:
        image_prompt = (
            f"Professional fashion editorial of a {subject}. "
            f"A {face_desc} wearing {outfit_str}. "
            f"{style_ctx}. "
            f"Occasion: {state.get('situation_text', 'casual')}. "
            f"Natural anatomy, correct head-to-body ratio (~1:7), full body shot, "
            f"studio background, high quality fashion photography."
        )

    logger.info("Generating composite: selfie=%s dresses=%d place=%s hash=%s",
                has_selfie, len(dress_images), has_place, cache_hash[:8])

    place_compressed = compress_image(place_bytes) if place_bytes else None

    def _build_contents(prompt: str, s_bytes, d_images, p_compressed) -> list:
        c: list = [prompt]
        if s_bytes:
            c.append(gtypes.Part.from_bytes(data=compress_image(s_bytes), mime_type="image/jpeg"))
        for d in d_images:
            c.append(gtypes.Part.from_bytes(data=d, mime_type="image/jpeg"))
        if p_compressed:
            c.append(gtypes.Part.from_bytes(data=p_compressed, mime_type="image/jpeg"))
        return c

    try:
        client = get_client()
        contents = _build_contents(image_prompt, selfie_bytes, dress_images, place_compressed)

        if len(contents) > 1:
            response = client.models.generate_content(
                model=settings.GEMINI_MODEL_IMAGE,
                contents=contents,
                config=gtypes.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
            )
        else:
            img_response = client.models.generate_images(
                model="imagen-4.0-generate-001",
                prompt=image_prompt,
                config=gtypes.GenerateImagesConfig(number_of_images=1),
            )
            image_bytes_fallback = img_response.generated_images[0].image.image_bytes if img_response.generated_images else None
            if not image_bytes_fallback:
                raise ValueError("No image from Imagen")
            image_url = await upload_image_to_storage(
                image_bytes_fallback, f"generated/{state['user_id']}/situation_{cache_hash[:12]}.jpg"
            )
            await cache_set(cache_key, image_url, settings.CACHE_TTL_IMAGE)
            return {"composite_image_url": image_url, "composite_image_url_2": ""}

        image_data: Optional[bytes] = None
        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.mime_type.startswith("image"):
                image_data = part.inline_data.data
                break

        if not image_data:
            raise ValueError("No image returned from model")

        image_url = await upload_image_to_storage(
            image_data, f"generated/{state['user_id']}/situation_{cache_hash[:12]}.jpg"
        )
        await cache_set(cache_key, image_url, settings.CACHE_TTL_IMAGE)

        # ── Generate second outfit image ───────────────────────────────────────
        rec2 = state.get("recommendation_2", {})
        image_url_2 = ""
        if rec2 and rec2.get("items"):
            try:
                items2 = rec2.get("items", [])
                outfit_parts2 = [f"{i.get('color','')} {i.get('description','')}" for i in items2[:4]]
                outfit_str2 = ", ".join(p for p in outfit_parts2 if p)

                # Fetch dress images for outfit 2
                dress_urls2 = [wardrobe_image_map.get(str(i.get("item_id", "")), "") for i in items2[:4]]
                fetched2 = await _asyncio.gather(*[_fetch(u) for u in dress_urls2], return_exceptions=True)
                dress_images2 = [compress_image(f, 512) for f in fetched2 if isinstance(f, bytes) and len(f) > 1000]

                ref2 = []; idx2 = 1
                if has_selfie:
                    ref2.append(f"IMAGE {idx2}: selfie — same person"); idx2 += 1
                for n2 in range(len(dress_images2)):
                    ref2.append(f"IMAGE {idx2+n2}: clothing item {n2+1} for this DIFFERENT outfit")
                idx2 += len(dress_images2)
                if has_place:
                    ref2.append(f"IMAGE {idx2}: same venue")

                prompt2 = (
                    f"Professional fashion editorial of a {subject}.\n"
                    + (f"Reference images:\n" + "\n".join(f"- {r}" for r in ref2) + "\n\n" if ref2 else "")
                    + f"DIFFERENT outfit from the previous look: {outfit_str2}\n"
                    f"{style_ctx}\nOccasion: {state.get('situation_text','casual')}\n"
                    f"Same rules: natural proportions, seamless photograph, full body shot."
                )
                contents2 = _build_contents(prompt2, selfie_bytes, dress_images2, place_compressed)
                resp2 = client.models.generate_content(
                    model=settings.GEMINI_MODEL_IMAGE,
                    contents=contents2,
                    config=gtypes.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
                )
                for part2 in resp2.candidates[0].content.parts:
                    if part2.inline_data and part2.inline_data.mime_type.startswith("image"):
                        image_url_2 = await upload_image_to_storage(
                            part2.inline_data.data,
                            f"generated/{state['user_id']}/situation2_{cache_hash[:12]}.jpg"
                        )
                        break
            except Exception as e2:
                logger.warning("Second outfit image failed: %s", e2)

        return {"composite_image_url": image_url, "composite_image_url_2": image_url_2}

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
            return {"composite_image_url": card_url, "composite_image_url_2": ""}

        placeholder = "https://placehold.co/512x768/7c3aed/white?text=Image+Generation+Failed"
        return {"composite_image_url": placeholder, "composite_image_url_2": ""}


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
        "recommendation_2": {},
        "composite_image_url": "",
        "composite_image_url_2": "",
        "errors": [],
    })
    return result
