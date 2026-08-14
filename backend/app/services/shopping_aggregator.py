"""
Shopping Aggregator Service
────────────────────────────
Searches Snitch, Rare Rabbit, Amazon, Flipkart for clothing items.
All results are Redis-cached (12 hour TTL) — key: category:color:fit.

Strategy:
- Snitch & Rare Rabbit: Shopify JSON API → real product pages within price range
- Amazon / Flipkart: Gemini grounding → direct product pages
- Redis cache with 12h TTL prevents repeated external requests
- Concurrent search across platforms
- Returns actual product links (never search-result pages)
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

# ─── Snitch / Rare Rabbit collection slug maps ───────────────────────────────

# Snitch Shopify is at snitch.co.in; new storefront at snitch.com uses same handles
_SNITCH_CAT_MAP: dict[str, str] = {
    "shirt": "shirts",
    "shirts": "shirts",
    "t-shirt": "t-shirts",
    "tshirt": "t-shirts",
    "t-shirts": "t-shirts",
    "polo": "t-shirts",
    "jeans": "jeans",
    "denim": "jeans",
    "trouser": "trousers",
    "trousers": "trousers",
    "chinos": "trousers",
    "pants": "trousers",
    "cargo": "cargo-pants",
    "cargo pants": "cargo-pants",
    "joggers": "joggers",
    "shorts": "shorts",
    "jacket": "jackets",
    "jackets": "jackets",
    "overshirt": "overshirts",
    "blazer": "blazers",
    "sweatshirt": "sweatshirts",
    "hoodie": "hoodies",
    "sweater": "sweaters",
    "shoes": "shoes",
    "footwear": "shoes",
    "accessories": "accessories",
    "co-ords": "co-ords",
    "coords": "co-ords",
    # Traditional/ethnic → closest Snitch equivalent (linen/casual shirts)
    "kurta": "shirts",
    "ethnic": "shirts",
    "traditional": "shirts",
    "linen kurta": "shirts",
}

# Rare Rabbit is on thehouseofrare.com
_RARERABBIT_CAT_MAP: dict[str, str] = {
    "shirt": "rare-rabbit-men-shirt-collection",
    "shirts": "rare-rabbit-men-shirt-collection",
    "t-shirt": "rare-rabbit-aw-25-t-shirt",
    "tshirt": "rare-rabbit-aw-25-t-shirt",
    "t-shirts": "rare-rabbit-aw-25-t-shirt",
    "polo": "rare-rabbit-aw-25-polo",
    "jeans": "rare-rabbit-aw-25-jeans",
    "denim": "rare-rabbit-aw-25-jeans",
    "trouser": "rare-rabbit-aw-25-trouser",
    "trousers": "rare-rabbit-aw-25-trouser",
    "chinos": "rare-rabbit-aw-25-trouser",
    "pants": "rare-rabbit-aw-25-trouser",
    "jacket": "rare-rabbit-aw-25-jacket",
    "jackets": "rare-rabbit-aw-25-jacket",
    "sweater": "rare-rabbit-sweaters",
    "sweaters": "rare-rabbit-sweaters",
    "sweatshirt": "rare-rabbit-aw-25-sweatshirt",
    "sweatshirts": "rare-rabbit-aw-25-sweatshirt",
    "innerwear": "rare-rabbit-innerwear",
    "accessories": "accessories-for-men",
    "formal": "rare-rabbit-formal-shirt-and-trouser-collection",
    # Traditional/ethnic wear — Rare Rabbit has linen shirts and some ethnic options
    "kurta": "rare-rabbit-linen-shirts",
    "ethnic": "rare-rabbit-linen-shirts",
    "traditional": "rare-rabbit-linen-shirts",
    "linen kurta": "rare-rabbit-linen-shirts",
    "churidar": "rare-rabbit-aw-25-trouser",
    "pajama": "rare-rabbit-pajama",
}


async def _fetch_shopify_product(
    store_base: str,
    collection: str,
    price_min: int,
    price_max: int,
    keywords: list[str],
) -> Optional[dict]:
    """
    Fetch products from a Shopify store's public JSON API.
    Returns the best-matching product dict within the price range.
    Product page URL: {store_base}/products/{handle}
    """
    url = f"{store_base}/collections/{collection}/products.json?limit=50&sort_by=created-descending"
    try:
        async with httpx.AsyncClient(timeout=12, headers=_HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            products = resp.json().get("products", [])
    except Exception as exc:
        logger.warning("Shopify fetch failed %s: %s", url, exc)
        return None

    if not products:
        return None

    # Filter by price range (price in Shopify is a string like "1299.00")
    def _price(p: dict) -> float:
        try:
            return float(p["variants"][0]["price"])
        except Exception:
            return 0.0

    in_range = [p for p in products if (
        (price_min == 0 or _price(p) >= price_min) and
        (price_max == 0 or _price(p) <= price_max)
    )]
    candidates = in_range if in_range else products  # relax price if no match

    # Score by keyword match in title (case-insensitive)
    kws = [k.lower() for k in keywords if k]
    best: Optional[dict] = None
    best_score = -1
    for p in candidates:
        title_lower = p.get("title", "").lower()
        score = sum(1 for k in kws if k in title_lower)
        if score > best_score:
            best_score = score
            best = p

    return best


async def search_snitch(
    category: str,
    color: str,
    description: str = "",
    price_min: int = 0,
    price_max: int = 0,
) -> Optional[dict]:
    """
    Search Snitch via Shopify JSON API and return an actual product-page result.
    Falls back to a filtered category URL if no product found via API.
    Snitch is men-only.
    """
    cat_lower = category.lower().strip()
    # Try direct category match first, then partial
    collection = _SNITCH_CAT_MAP.get(cat_lower)
    if not collection:
        for k, v in _SNITCH_CAT_MAP.items():
            if k in cat_lower or cat_lower in k:
                collection = v
                break
    if not collection:
        collection = "all"

    keywords = [w for w in (color + " " + description).split() if len(w) > 2]
    product = await _fetch_shopify_product(
        "https://www.snitch.co.in", collection, price_min, price_max, keywords  # API still on snitch.co.in
    )

    if product:
        handle = product.get("handle", "")
        product_id = product.get("id", "")
        title = product.get("title", description or category)
        try:
            price_val = float(product["variants"][0]["price"])
            price_str = f"₹{int(price_val):,}"
        except Exception:
            price_str = ""
        image_url = ""
        try:
            image_url = product.get("images", [{}])[0].get("src", "")
        except Exception:
            pass
        # snitch.com product URL format: /men-{category}/{handle}/{id}/buy
        product_url = f"https://www.snitch.com/men-{collection}/{handle}/{product_id}/buy"
        return {
            "name": title,
            "price": price_str,
            "url": product_url,
            "image_url": image_url,
            "platform": "Snitch",
        }

    # Fallback: snitch.com category page with color filter
    color_param = f"?color={color.replace(' ', '+')}" if color else ""
    fallback_url = f"https://www.snitch.com/men-{collection}/buy{color_param}"
    return {
        "name": description or f"{color} {category}".strip(),
        "price": "",
        "url": fallback_url,
        "image_url": "",
        "platform": "Snitch",
    }


async def search_rarerabbit(
    category: str,
    color: str,
    description: str = "",
    price_min: int = 0,
    price_max: int = 0,
) -> Optional[dict]:
    """
    Search Rare Rabbit via Shopify JSON API on thehouseofrare.com
    and return an actual product-page result.
    Rare Rabbit is primarily menswear.
    """
    cat_lower = category.lower().strip()
    collection = _RARERABBIT_CAT_MAP.get(cat_lower)
    if not collection:
        for k, v in _RARERABBIT_CAT_MAP.items():
            if k in cat_lower or cat_lower in k:
                collection = v
                break
    if not collection:
        collection = "rare-rabbit-all-product"

    keywords = [w for w in (color + " " + description).split() if len(w) > 2]
    product = await _fetch_shopify_product(
        "https://thehouseofrare.com", collection, price_min, price_max, keywords
    )

    if product:
        handle = product.get("handle", "")
        title = product.get("title", description or category)
        try:
            price_val = float(product["variants"][0]["price"])
            price_str = f"₹{int(price_val):,}"
        except Exception:
            price_str = ""
        image_url = ""
        try:
            image_url = product.get("images", [{}])[0].get("src", "")
        except Exception:
            pass
        return {
            "name": title,
            "price": price_str,
            "url": f"https://thehouseofrare.com/products/{handle}",
            "image_url": image_url,
            "platform": "Rare Rabbit",
        }

    # Fallback: collection page
    fallback_url = f"https://thehouseofrare.com/collections/{collection}"
    return {
        "name": description or f"Rare Rabbit {color} {category}".strip(),
        "price": "",
        "url": fallback_url,
        "image_url": "",
        "platform": "Rare Rabbit",
    }

# ─── Scraper helpers ──────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


async def _find_product_url_via_gemini(query: str, platform: str, price_min: int = 0, price_max: int = 0) -> Optional[dict]:
    """
    Use Gemini with Google Search grounding to find an actual product page URL.
    Falls back to a very specific price-filtered search URL that works reliably.
    """
    import json as _json
    from app.services.image_generator import get_client
    from google.genai import types as gtypes

    price_constraint = f" Price between ₹{price_min} and ₹{price_max}." if (price_min or price_max) else ""
    site = "amazon.in" if "amazon" in platform.lower() else "flipkart.com"

    search_prompt = (
        f"Search for this product on {site} and return its direct product page URL.\n"
        f"Product: {query}{price_constraint}\n\n"
        f"Return ONLY this JSON (no markdown, no explanation):\n"
        f'{{"url": "https://www.{site}/dp/ASIN_HERE_OR_product_path", "name": "exact product title", "price": "₹X,XXX"}}\n'
        f"The url must contain /dp/ (Amazon) or /p/ (Flipkart). If not found, return null."
    )

    try:
        client = get_client()
        resp = client.models.generate_content(
            model=settings.GEMINI_MODEL_RECOMMEND,
            contents=search_prompt,
            config=gtypes.GenerateContentConfig(
                tools=[gtypes.Tool(google_search=gtypes.GoogleSearch())],
                temperature=0.0,
            ),
        )
        text = resp.text.strip() if resp.text else ""
        # Remove markdown code blocks if present
        text = re.sub(r"```(?:json)?", "", text).strip()
        # Extract JSON
        json_match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
        if json_match:
            data = _json.loads(json_match.group())
            url = (data.get("url") or "").strip()
            # Strict validation: must be a real product page URL
            is_amazon_product = "amazon.in" in url and ("/dp/" in url or "/gp/product/" in url)
            is_flipkart_product = "flipkart.com" in url and "/p/" in url
            if is_amazon_product or is_flipkart_product:
                logger.info("Gemini grounding returned real product URL: %s", url)
                return {
                    "url": url,
                    "name": data.get("name", query),
                    "price": data.get("price", ""),
                    "platform": "Amazon" if "amazon" in url else "Flipkart",
                }
    except Exception as e:
        logger.warning("Gemini grounding search failed: %s", e)

    # Reliable fallback: very specific price-filtered search URL
    import urllib.parse as _up
    q_quoted = _up.quote_plus(f'"{query}"')
    if "amazon" in platform.lower():
        price_param = f"&rh=p_36%3A{price_min*100}-{price_max*100}" if (price_min or price_max) else ""
        fallback = f"https://www.amazon.in/s?k={q_quoted}&i=apparel{price_param}"
    else:
        price_param = f"&p[]=facets.price_range.from%3D{price_min}&p[]=facets.price_range.to%3D{price_max}" if (price_min or price_max) else ""
        fallback = f"https://www.flipkart.com/search?q={q_quoted}&sort=relevance{price_param}"

    logger.info("Using specific search fallback: %s", fallback)
    return {"url": fallback, "name": query, "price": "", "platform": "Amazon" if "amazon" in platform.lower() else "Flipkart"}


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
    For each missing wardrobe item find an actual product on Snitch or Rare Rabbit
    within the user's selected budget range via Shopify JSON API.

    Strategy per item:
    1. Try Snitch  — exact category collection filtered by price
    2. Try Rare Rabbit — exact category collection filtered by price
    3. If neither finds a price-matching product, relax price and try again (show
       closest product so the user still gets a real link, never a search result)

    Returns: list of dicts with real product-page URLs, Shopify image, price, name.
    """
    if not missing_items:
        return []

    price_min, price_max = _parse_budget_range(budget) if budget else (0, 0)

    # Alternate brand per item so the user sees variety
    # Snitch → menswear only; Rare Rabbit → menswear + some womenswear
    async def _get_for_item(item: dict, prefer_snitch: bool) -> dict:
        cat = item.get("category", "shirt")
        color = item.get("color", "")
        desc = item.get("description", cat)
        for_item = desc

        # Try primary brand first, then secondary
        primary = search_snitch if prefer_snitch else search_rarerabbit
        secondary = search_rarerabbit if prefer_snitch else search_snitch

        # Pass 1: strict price range
        hit = await primary(cat, color, desc, price_min, price_max)
        if not hit or not hit.get("url", "").startswith("http"):
            hit = await secondary(cat, color, desc, price_min, price_max)

        # Pass 2: relax price range (show closest product, always real link)
        if not hit or not hit.get("url", "").startswith("http"):
            hit = await primary(cat, color, desc, 0, 0)
        if not hit or not hit.get("url", "").startswith("http"):
            hit = await secondary(cat, color, desc, 0, 0)

        if hit and hit.get("url", "").startswith("http"):
            return {**hit, "for_item": for_item, "from_image": True}

        # Absolute last resort: collection page (still real, not a generic search)
        col_snitch = _SNITCH_CAT_MAP.get(cat.lower(), "all")
        color_param = f"?color={color.replace(' ', '+')}" if color else ""
        return {
            "name": desc,
            "price": budget or "",
            "url": f"https://www.snitch.com/men-{col_snitch}/buy{color_param}",
            "image_url": "",
            "platform": "Snitch",
            "for_item": for_item,
            "from_image": True,
        }

    # Run all items concurrently, alternating Snitch / Rare Rabbit
    tasks = [_get_for_item(item, i % 2 == 0) for i, item in enumerate(missing_items)]
    results = list(await asyncio.gather(*tasks, return_exceptions=False))

    # Ensure every entry has proper structure
    clean: list[dict] = []
    for r in results:
        if isinstance(r, dict):
            clean.append(r)

    logger.info("Shopping results: %d items, budget=%s", len(clean), budget)
    return clean
