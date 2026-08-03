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
import re
from typing import Optional
import httpx

from app.core.config import get_settings
from app.services.cache_service import cache_get, cache_set, retail_search_key

settings = get_settings()
logger = logging.getLogger(__name__)

# ─── Scraper helpers ──────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


async def _find_product_url_via_gemini(query: str, platform: str, price_min: int = 0, price_max: int = 0) -> Optional[dict]:
    """
    Use Gemini with Google Search grounding to find an actual product page URL.
    Gemini searches Google in real-time → extracts the exact Amazon/Flipkart product URL.
    No bot detection issues — uses Gemini API, not direct scraping.
    """
    import json as _json
    from app.services.image_generator import get_client
    from google.genai import types as gtypes

    price_constraint = f" Price must be between ₹{price_min} and ₹{price_max}." if (price_min or price_max) else ""
    site = "amazon.in" if "amazon" in platform.lower() else "flipkart.com"

    search_prompt = (
        f"Search for this exact product on {site} and return its direct product page URL.\n"
        f"Product: {query}\n"
        f"{price_constraint}\n"
        f"Return ONLY a JSON object:\n"
        f'{{"url": "https://www.{site}/...", "name": "product title", "price": "₹X,XXX"}}\n'
        f"The URL must be a direct product page link (e.g., amazon.in/dp/ASIN or flipkart.com/product/p/itemid), NOT a search page."
    )

    try:
        client = get_client()
        resp = client.models.generate_content(
            model=settings.GEMINI_MODEL_RECOMMEND,
            contents=search_prompt,
            config=gtypes.GenerateContentConfig(
                tools=[gtypes.Tool(google_search=gtypes.GoogleSearch())],
                temperature=0.1,
            ),
        )
        text = resp.text.strip()
        # Extract JSON from response
        json_match = re.search(r'\{[^}]+\}', text, re.DOTALL)
        if json_match:
            data = _json.loads(json_match.group())
            url = data.get("url", "")
            # Validate it's actually a product page, not search
            if url and ("/dp/" in url or "/p/" in url or "/product/" in url):
                logger.info("Gemini grounding found real product URL: %s", url)
                return {
                    "url": url,
                    "name": data.get("name", query),
                    "price": data.get("price", ""),
                    "platform": "Amazon" if "amazon" in url else "Flipkart",
                }
    except Exception as e:
        logger.warning("Gemini grounding search failed: %s", e)
    return None

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
    """Google Shopping with price filter — always real, always shows actual products."""
    import urllib.parse
    q = urllib.parse.quote_plus(query)
    tbs_parts = ["mr:1"]
    if price_min > 0 or price_max > 0:
        tbs_parts.append("price:1")
        if price_min > 0:
            tbs_parts.append(f"ppr_min:{price_min}")
        if price_max > 0:
            tbs_parts.append(f"ppr_max:{price_max}")
    tbs = ",".join(tbs_parts)
    return f"https://www.google.com/search?q={q}+site%3Amyntra.com+OR+site%3Aflipkart.com+OR+site%3Aamazon.in&tbm=shop&tbs={tbs}&hl=en&gl={country}"


def _platform_shopping_url(query: str, platform: str, gender: str = "men", price_min: int = 0, price_max: int = 0) -> str:
    """Platform-specific search URL with price range filter — direct to filtered results."""
    import urllib.parse
    q_plus = urllib.parse.quote_plus(query)

    if platform == "Myntra":
        price_filter = f"&f=Price%3A{price_min}+TO+{price_max}" if price_min or price_max else ""
        return f"https://www.myntra.com/{gender}?rawQuery={q_plus}&sort=popularity{price_filter}"
    if platform == "Flipkart":
        price_filter = ""
        if price_min or price_max:
            price_filter = f"&p[]=facets.price_range.from%3D{price_min}&p[]=facets.price_range.to%3D{price_max}"
        return f"https://www.flipkart.com/search?q={q_plus}&sort=relevance{price_filter}"
    if platform == "AJIO":
        price_filter = f"&ft=price[{price_min}]+to+[{price_max}]" if price_min or price_max else ""
        return f"https://www.ajio.com/search/?text={q_plus}&gender={gender}{price_filter}"
    # Amazon with price range in paise (₹1 = 100 paise)
    price_filter = ""
    if price_min or price_max:
        lo = price_min * 100 if price_min else 1
        hi = price_max * 100 if price_max else 9999999
        price_filter = f"&rh=p_36%3A{lo}-{hi}"
    return f"https://www.amazon.in/s?k={q_plus}&i=apparel{price_filter}"


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

For EACH item, suggest EXACTLY 2 specific, real products available on Amazon India or Flipkart.
Include the brand name and exact product model/style/colour.

Return ONLY a JSON array ({len(missing_items) * 2} entries total, 2 per item):
[
  {{
    "for_item": "exact description of the item this satisfies",
    "brand": "Brand Name (e.g. Levis, HRX, Wrogn, Jack & Jones, Roadster, Adidas)",
    "product_name": "Exact model name with colour (e.g. 511 Slim Fit Mid-Rise Navy Stretch Jeans)",
    "estimated_price": "\u20b9X,XXX",
    "search_query": "Brand + exact model name + colour + gender — specific enough to find THIS product on Amazon/Flipkart",
    "platform": "Amazon|Flipkart"
  }}
]

STRICT RULES:
- Use ONLY Amazon India or Flipkart
- Be VERY SPECIFIC: 'Levis 511 Slim Fit Navy Stretch Jeans Men' NOT 'blue jeans'
- The search_query must find this EXACT product, not 100s of results
{f"- Price MUST be within {budget}" if budget else ""}
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
            platform = p.get("platform", "Amazon")
            name = f"{p.get('brand','')} — {p.get('product_name','')}".strip(" —") or search_q
            price_str = p.get("estimated_price", "")
            for_item = p.get("for_item", "")

            # Use Gemini with Google Search grounding to find real product page URL
            scraped = None
            try:
                scraped = await _find_product_url_via_gemini(search_q, platform, price_min, price_max)
            except Exception:
                pass

            if scraped:
                results.append({
                    "name": scraped.get("name", name),
                    "price": scraped.get("price") or price_str,
                    "url": scraped["url"],
                    "image_url": "",
                    "platform": scraped["platform"],
                    "for_item": for_item,
                    "from_image": True,
                })
            else:
                # Fallback: price-filtered platform search
                fallback_url = _platform_shopping_url(search_q, platform, gender_word, price_min, price_max)
                results.append({
                    "name": name,
                    "price": price_str,
                    "url": fallback_url,
                    "image_url": "",
                    "platform": platform,
                    "for_item": for_item,
                    "from_image": True,
                })

    except Exception as e:
        logger.error("get_specific_product_links failed: %s", e)
        # Fallback: build price-filtered platform URLs from item descriptions
        for i, item in enumerate(missing_items):
            query = f"{item.get('color','')} {item.get('description', item.get('category',''))} {gender_word}".strip()
            platform = ["Myntra", "Flipkart", "AJIO", "Amazon"][i % 4]
            url = _platform_shopping_url(query, platform, gender_word, price_min, price_max)
            results.append({
                "name": item.get("description", item.get("category", "Product")),
                "price": budget or "",
                "url": url,
                "image_url": "",
                "platform": platform,
                "for_item": item.get("description", ""),
                "from_image": True,
            })

    return results
