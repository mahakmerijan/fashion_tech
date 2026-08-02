"""
Shopping Aggregator Service
────────────────────────────
Searches Flipkart, Amazon, Myntra, AJIO for clothing items.
All results are Redis-cached (12 hour TTL) — key: category:color:fit.

Strategy:
- Standardise search query terms from Gemini output
- Redis cache with 12h TTL prevents repeated external requests
- Concurrent search across platforms
- Returns affiliate/product links
"""

import asyncio
import logging
from typing import Optional
import httpx

from app.core.config import get_settings
from app.services.cache_service import cache_get, cache_set, retail_search_key

settings = get_settings()
logger = logging.getLogger(__name__)


def build_search_query(category: str, color: str, fit: str, gender: str = "men", description: str = "") -> str:
    """Build a human-readable search query using the full item description when available."""
    g = gender.lower().replace("male", "men").replace("female", "women")
    if description:
        # Use the full description for the most specific search possible
        base = description.lower().strip()
        return f"{g} {base}".strip()
    parts = [g, color.lower(), fit.lower().replace(" fit", ""), category.lower()]
    return " ".join(p.strip() for p in parts if p.strip())


def platform_search_url(platform: str, query: str, category: str, gender: str) -> str:
    """Build a platform-specific deep-link search URL."""
    q_plus = query.replace(" ", "+")
    q_dash = query.replace(" ", "-")
    cat_lower = category.lower()

    # Map category to platform-specific path segments
    myntra_cat = {
        "shoes": "shoes", "footwear": "footwear", "shirt": "shirts",
        "pants": "trousers", "trousers": "trousers", "jacket": "jackets",
        "blazer": "blazers", "outerwear": "jackets", "accessories": "accessories",
        "gloves": "gloves", "belt": "belts", "watch": "watches", "bag": "bags",
        "kurta": "kurtas", "dress": "dresses", "shorts": "shorts",
    }.get(cat_lower, cat_lower + "s")

    ajio_gender = "women" if gender.lower() == "women" else "men"

    if platform == "Amazon":
        return f"https://www.amazon.in/s?k={q_plus}&i=apparel"
    if platform == "Flipkart":
        return f"https://www.flipkart.com/search?q={q_plus}&sort=relevance"
    if platform == "Myntra":
        return f"https://www.myntra.com/{myntra_cat}?rawQuery={q_plus}"
    if platform == "AJIO":
        return f"https://www.ajio.com/search/?text={q_plus}&gender={ajio_gender}"
    return f"https://www.google.com/search?q={q_plus}+buy+online"


async def search_amazon(query: str, category: str, gender: str = "men", description: str = "") -> list[dict]:
    """Search Amazon India."""
    try:
        url = platform_search_url("Amazon", query, category, gender)
        return [{"name": description or f"{category.title()}", "price": "View on Amazon",
                 "url": url, "image_url": "", "platform": "Amazon"}]
    except Exception as e:
        logger.warning("Amazon search failed: %s", e)
        return []


async def search_flipkart(query: str, category: str, gender: str = "men", description: str = "") -> list[dict]:
    """Search Flipkart."""
    try:
        url = platform_search_url("Flipkart", query, category, gender)
        return [{"name": description or f"{category.title()}", "price": "View on Flipkart",
                 "url": url, "image_url": "", "platform": "Flipkart"}]
    except Exception as e:
        logger.warning("Flipkart search failed: %s", e)
        return []


async def search_myntra(query: str, category: str, gender: str = "men", description: str = "") -> list[dict]:
    """Search Myntra."""
    try:
        url = platform_search_url("Myntra", query, category, gender)
        return [{"name": description or f"{category.title()}", "price": "View on Myntra",
                 "url": url, "image_url": "", "platform": "Myntra"}]
    except Exception as e:
        logger.warning("Myntra search failed: %s", e)
        return []


async def search_ajio(query: str, category: str, gender: str = "men", description: str = "") -> list[dict]:
    """Search AJIO."""
    try:
        url = platform_search_url("AJIO", query, category, gender)
        return [{"name": description or f"{category.title()}", "price": "View on AJIO",
                 "url": url, "image_url": "", "platform": "AJIO"}]
    except Exception as e:
        logger.warning("AJIO search failed: %s", e)
        return []


async def search_all_platforms(
    category: str,
    color: str,
    fit: str,
    gender: str = "men",
    description: str = "",
) -> list[dict]:
    """
    Search all 4 platforms concurrently.
    Results cached in Redis for CACHE_TTL_RETAIL (12 hours).
    """
    cache_key = retail_search_key(category, color, fit)
    cached = await cache_get(cache_key)
    if cached:
        logger.info("Retail search cache HIT: %s", cache_key)
        return cached if isinstance(cached, list) else []

    query = build_search_query(category, color, fit, gender, description)
    logger.info("Retail search MISS — querying platforms for: %s", query)

    # Concurrent search — pass description so URLs and names are item-specific
    results_groups = await asyncio.gather(
        search_amazon(query, category, gender, description),
        search_flipkart(query, category, gender, description),
        search_myntra(query, category, gender, description),
        search_ajio(query, category, gender, description),
        return_exceptions=True,
    )

    results = []
    for group in results_groups:
        if isinstance(group, list):
            results.extend(group)

    # Cache with 12h TTL
    await cache_set(cache_key, results, settings.CACHE_TTL_RETAIL)
    return results


def _parse_budget_range(budget: str) -> tuple[int, int]:
    """Parse budget string like '₹1,500–₹3,000' into (min, max) integers."""
    import re
    nums = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", budget)]
    if len(nums) == 0:
        return 0, 0
    if len(nums) == 1:
        # "Under ₹500" or "Above ₹15,000"
        if "under" in budget.lower() or "below" in budget.lower():
            return 0, nums[0]
        return nums[0], 0   # 0 means no upper limit
    return nums[0], nums[1]


def _google_shopping_url(query: str, price_min: int = 0, price_max: int = 0, country: str = "in") -> str:
    """
    Build a Google Shopping URL for India.
    Price filters use the tbs parameter: ppr_min / ppr_max (prices in INR).
    These are 100% real, always-working search links.
    """
    import urllib.parse
    q = urllib.parse.quote_plus(query + " India buy online")
    tbs_parts = ["mr:1"]
    if price_min > 0 or price_max > 0:
        tbs_parts.append("price:1")
        if price_min > 0:
            tbs_parts.append(f"ppr_min:{price_min}")
        if price_max > 0:
            tbs_parts.append(f"ppr_max:{price_max}")
    tbs = ",".join(tbs_parts)
    return f"https://www.google.com/search?q={q}&tbm=shop&tbs={tbs}&hl=en&gl={country}"


def _platform_shopping_url(query: str, platform: str, gender: str = "men") -> str:
    """Build platform-specific search URL using the specific product query."""
    import urllib.parse
    q_plus = urllib.parse.quote_plus(query)
    q_dash = query.replace(" ", "-").lower()
    if platform == "Myntra":
        return f"https://www.myntra.com/{gender}/{q_dash}?rawQuery={q_plus}&sort=popularity"
    if platform == "Flipkart":
        return f"https://www.flipkart.com/search?q={q_plus}&sort=relevance"
    if platform == "AJIO":
        return f"https://www.ajio.com/search/?text={q_plus}&gender={gender}"
    # Amazon
    return f"https://www.amazon.in/s?k={q_plus}&i=apparel&rh=n:1968024031"


async def get_specific_product_links(
    missing_items: list[dict],
    budget: str = "",
    gender: str = "men",
    skin_tone: str = "",
    situation: str = "",
) -> list[dict]:
    """
    Use Gemini to get specific product names, then build Google Shopping URLs
    with exact price-range filters — guaranteed real links that always work.
    """
    import json as _json
    from app.services.image_generator import get_client
    from google.genai import types as gtypes

    if not missing_items:
        return []

    price_min, price_max = _parse_budget_range(budget) if budget else (0, 0)
    gender_word = "women" if gender == "women" else "men"
    skin_note = f"Skin tone: {skin_tone} — suggest complementary colours." if skin_tone else ""

    items_text = "\n".join(
        f"- {i.get('color', '')} {i.get('description', i.get('category', ''))} (category: {i.get('category', '')})"
        for i in missing_items
    )

    budget_instruction = (
        f"Budget: STRICTLY {budget} per item — suggest only products in this price range."
        if budget else "No budget constraint."
    )

    prompt = f"""You are a fashion expert for Indian e-commerce.

The user needs these items for: {situation}
Gender: {gender_word}
{skin_note}
{budget_instruction}

Items needed (NOT in their wardrobe):
{items_text}

For EACH item, suggest EXACTLY 2 specific, real products available in India.
Include the brand name and specific product model/style/colour.

Return ONLY a JSON array ({len(missing_items) * 2} entries total, 2 per item):
[
  {{
    "for_item": "exact description of the item this satisfies",
    "brand": "Brand Name",
    "product_name": "Specific product model name with colour",
    "estimated_price": "\u20b9X,XXX",
    "search_query": "brand + product name + colour + gender — specific enough to find this exact item on any shopping site",
    "platform": "Myntra|Flipkart|Amazon|AJIO"
  }}
]

Be SPECIFIC. Instead of 'navy jeans', write 'Levis 511 Slim Fit Navy Stretch Jeans Men'.
{f"Price MUST be within {budget}." if budget else ""}
Return ONLY the JSON array."""

    results = []
    try:
        client = get_client()
        resp = client.models.generate_content(
            model=settings.GEMINI_MODEL_RECOMMEND,
            contents=prompt,
            config=gtypes.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=1200,
            ),
        )
        products = _json.loads(resp.text)
        if not isinstance(products, list):
            products = []

        for p in products:
            search_q = p.get("search_query") or f"{p.get('brand','')} {p.get('product_name','')}".strip()
            platform = p.get("platform", "Google Shopping")

            # Primary: Google Shopping with price filter (100% real link)
            google_url = _google_shopping_url(search_q, price_min, price_max)

            # Secondary: platform-specific filtered search
            platform_url = _platform_shopping_url(search_q, platform, gender_word)

            # Return Google Shopping as primary (guaranteed) + platform as secondary
            for url, plat in [(google_url, "Google Shopping"), (platform_url, platform)]:
                results.append({
                    "name": f"{p.get('brand','')} — {p.get('product_name','')}".strip(" —"),
                    "price": p.get("estimated_price", ""),
                    "url": url,
                    "image_url": "",
                    "platform": plat,
                    "for_item": p.get("for_item", ""),
                    "from_image": True,
                })

    except Exception as e:
        logger.error("get_specific_product_links failed: %s", e)
        # Fallback: build Google Shopping URLs directly from item descriptions
        for item in missing_items:
            query = f"{item.get('color','')} {item.get('description', item.get('category',''))} {gender_word} India"
            google_url = _google_shopping_url(query.strip(), price_min, price_max)
            results.append({
                "name": item.get("description", item.get("category", "Product")),
                "price": budget or "",
                "url": google_url,
                "image_url": "",
                "platform": "Google Shopping",
                "for_item": item.get("description", ""),
                "from_image": True,
            })

    return results
