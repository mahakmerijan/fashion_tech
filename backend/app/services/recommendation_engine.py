"""
Recommendation Engine — LangGraph Pipeline
───────────────────────────────────────────
Nodes:
  1. load_context   → fetch user profile + wardrobe from Redis/DB
  2. rule_filter    → apply hard rules (formality, season, occasion)
  3. build_prompt   → construct compact Gemini prompt (text only, no images)
  4. llm_reason     → Gemini 2.5 Flash (cached context window)
  5. format_output  → parse & structure output

Token strategy:
- Profile & wardrobe passed as pre-built text (from Redis cache)
- No raw images sent to Gemini during recommendations
- ≈95% token reduction vs sending images directly
"""

import json
import logging
import uuid
from typing import TypedDict, Optional, Annotated
import operator

from google import genai  # new google-genai SDK
from langgraph.graph import StateGraph, END

from app.core.config import get_settings
from app.services.cache_service import (
    cache_get, cache_set, profile_context_key,
    build_and_cache_profile_context,
)

settings = get_settings()
logger = logging.getLogger(__name__)

_genai_client = None

def get_genai_client():
    global _genai_client
    if _genai_client is None:
        from google import genai as _genai
        _genai_client = _genai.Client(api_key=settings.GEMINI_API_KEY)
    return _genai_client


# ─── State ────────────────────────────────────────────────────────────────────

class RecommendationState(TypedDict):
    user_id: str
    occasion: str
    profile: dict
    wardrobe: list[dict]
    profile_context: str
    filtered_wardrobe: list[dict]
    prompt: str
    llm_response: str
    recommendations: list[dict]
    errors: Annotated[list[str], operator.add]


# ─── Node implementations ─────────────────────────────────────────────────────

async def load_context(state: RecommendationState) -> dict:
    """Load profile context string from Redis cache (or rebuild from DB data)."""
    user_id = state["user_id"]
    key = profile_context_key(user_id)
    cached_ctx = await cache_get(key)

    if cached_ctx:
        logger.info("Profile context cache HIT for user %s", user_id[:8])
        return {"profile_context": cached_ctx}

    # Rebuild from profile + wardrobe passed in state
    logger.info("Profile context cache MISS — rebuilding for user %s", user_id[:8])
    ctx = await build_and_cache_profile_context(
        user_id, state.get("profile", {}), state.get("wardrobe", [])
    )
    return {"profile_context": ctx}


def rule_filter(state: RecommendationState) -> dict:
    """
    Rule engine: filter wardrobe to occasion-appropriate items.
    No LLM calls — pure business logic, zero token cost.
    """
    occasion = state["occasion"].lower()
    wardrobe = state.get("wardrobe", [])

    formality_floor = 0.0
    formality_ceil = 1.0
    required_categories: set[str] = set()

    # Occasion → formality rules
    if any(kw in occasion for kw in ["interview", "formal", "business", "corporate", "meeting"]):
        formality_floor = 0.65
        required_categories = {"Shirt", "Pants", "Shoes"}
    elif any(kw in occasion for kw in ["wedding", "gala", "dinner", "award"]):
        formality_floor = 0.75
    elif any(kw in occasion for kw in ["party", "club", "night"]):
        formality_floor = 0.4
        formality_ceil = 0.85
    elif any(kw in occasion for kw in ["gym", "sport", "workout", "athletic"]):
        formality_ceil = 0.3
    elif any(kw in occasion for kw in ["casual", "college", "travel", "beach"]):
        formality_ceil = 0.55

    filtered = [
        item for item in wardrobe
        if formality_floor <= float(item.get("formality_score", 0.3)) <= formality_ceil
    ]

    # If nothing passes the filter, relax and return all
    if not filtered:
        filtered = wardrobe

    return {"filtered_wardrobe": filtered}


def build_prompt(state: RecommendationState) -> dict:
    """
    Build a compact text-only Gemini prompt.
    Context already cached in Redis — we just insert it.
    """
    occasion = state["occasion"]
    context = state["profile_context"]
    filtered = state.get("filtered_wardrobe", [])

    # Compact wardrobe list for this request
    wardrobe_snippet = "\n".join(
        f"  [{i+1}] {item.get('primary_color', '')} {item.get('sub_category') or item.get('category', 'Item')} "
        f"(ID:{item.get('item_id', i)}, formality:{item.get('formality_score', 0.3):.1f})"
        for i, item in enumerate(filtered[:30])  # cap at 30 items
    )

    prompt = f"""You are an expert personal fashion stylist AI.

{context}

OCCASION: {occasion}

AVAILABLE WARDROBE ITEMS (pre-filtered for occasion):
{wardrobe_snippet or "No wardrobe items available — suggest purchases."}

TASK: Create 3 complete outfit recommendations. For each outfit:
1. Select items from the wardrobe (by ID) OR note items to buy
2. Provide a title and styling rationale
3. List 3-5 styling tips
4. List colour coordination suggestions
5. Flag missing items that should be purchased

Respond ONLY with valid JSON:
{{
  "recommendations": [
    {{
      "outfit_id": "unique_id",
      "title": "Outfit Name",
      "rationale": "Why this works for the occasion",
      "items": [
        {{
          "item_id": "ID_from_wardrobe_or_null",
          "category": "Shirt",
          "description": "White Linen Slim-fit Shirt",
          "color": "White",
          "from_wardrobe": true
        }}
      ],
      "missing_items": [
        {{
          "category": "Shoes",
          "description": "Oxford Formal Shoes",
          "color": "Black",
          "from_wardrobe": false
        }}
      ],
      "styling_tips": ["tip 1", "tip 2"],
      "color_suggestions": ["Navy + White is a classic power combination"],
      "formality_match": "High"
    }}
  ]
}}"""

    return {"prompt": prompt}


async def llm_reason(state: RecommendationState) -> dict:
    """Call Gemini 2.5 Flash with the compact prompt. No image tokens."""
    try:
        from google.genai import types as gtypes
        client = get_genai_client()
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL_RECOMMEND,
            contents=state["prompt"],
            config=gtypes.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3,
                max_output_tokens=4096,
            ),
        )
        return {"llm_response": response.text}
    except Exception as e:
        logger.error("LLM reasoning failed: %s", e)
        return {"llm_response": "", "errors": [f"LLM error: {e}"]}


def format_output(state: RecommendationState) -> dict:
    """Parse LLM JSON response and ensure consistent output structure."""
    try:
        data = json.loads(state["llm_response"])
        recs = data.get("recommendations", [])
        # Assign UUIDs if missing
        for rec in recs:
            if not rec.get("outfit_id"):
                rec["outfit_id"] = str(uuid.uuid4())
        return {"recommendations": recs}
    except Exception as e:
        logger.error("Failed to parse LLM output: %s\nRaw: %s", e, state.get("llm_response", "")[:200])
        return {"recommendations": [], "errors": [f"Parse error: {e}"]}


# ─── Graph ────────────────────────────────────────────────────────────────────

def build_recommendation_graph():
    graph = StateGraph(RecommendationState)

    graph.add_node("load_context", load_context)
    graph.add_node("rule_filter", rule_filter)
    graph.add_node("build_prompt", build_prompt)
    graph.add_node("llm_reason", llm_reason)
    graph.add_node("format_output", format_output)

    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "rule_filter")
    graph.add_edge("rule_filter", "build_prompt")
    graph.add_edge("build_prompt", "llm_reason")
    graph.add_edge("llm_reason", "format_output")
    graph.add_edge("format_output", END)

    return graph.compile()


# Singleton graph instance
_recommendation_graph = None


def get_recommendation_graph():
    global _recommendation_graph
    if _recommendation_graph is None:
        _recommendation_graph = build_recommendation_graph()
    return _recommendation_graph


async def get_recommendations(
    user_id: str,
    occasion: str,
    profile: dict,
    wardrobe: list[dict],
) -> list[dict]:
    """Public API — run the LangGraph pipeline and return outfit recommendations."""
    graph = get_recommendation_graph()
    initial_state: RecommendationState = {
        "user_id": user_id,
        "occasion": occasion,
        "profile": profile,
        "wardrobe": wardrobe,
        "profile_context": "",
        "filtered_wardrobe": [],
        "prompt": "",
        "llm_response": "",
        "recommendations": [],
        "errors": [],
    }
    result = await graph.ainvoke(initial_state)
    return result.get("recommendations", [])
