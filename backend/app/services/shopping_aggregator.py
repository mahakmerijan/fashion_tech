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
