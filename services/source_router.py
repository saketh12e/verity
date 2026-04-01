"""
Source Router — V3
Routes each crawler angle to the right search client.
Tavily: academic, news, temporal content (include_domains support).
Firecrawl: forums, practitioner communities, structured site crawl.
Contrarian: both clients, results merged and deduplicated.
Breaking news (lookback_hours <= 24): both clients run simultaneously.

Uses AsyncTavilyClient for non-blocking concurrent searches.
"""
import os
import asyncio
import logging
from dotenv import load_dotenv
from tavily import AsyncTavilyClient
from services.firecrawl_client import search as firecrawl_search

load_dotenv()
logger = logging.getLogger(__name__)

_tavily: AsyncTavilyClient | None = None


def _get_tavily() -> AsyncTavilyClient:
    global _tavily
    if _tavily is None:
        _tavily = AsyncTavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _tavily


# ── Routing table — angle → search strategy ─────────────────────────────────

ROUTING_TABLE: dict[str, dict] = {
    "scientific": {
        "client": "tavily",
        "query_suffix": "peer reviewed study systematic review meta-analysis evidence",
        "tavily_params": {
            "search_depth": "advanced",
            "max_results": 12,
            "include_domains": [
                "pubmed.ncbi.nlm.nih.gov", "arxiv.org", "nature.com",
                "thelancet.com", "nejm.org", "jamanetwork.com",
                "bmj.com", "cochrane.org", "cell.com", "science.org",
            ],
        },
    },
    "recent_news": {
        "client": "tavily",
        # query_suffix uses dynamic year — see route_and_search() for construction
        "query_suffix": "latest findings update",
        "tavily_params": {
            "search_depth": "advanced",
            "max_results": 12,
            "topic": "news",
            # time_range is computed dynamically from lookback_hours in route_and_search()
        },
    },
    "practitioner": {
        # Use both: Firecrawl handles Reddit/community forums (JS-rendered),
        # Tavily finds practitioner blogs, surveys, and professional publications.
        "client": "both",
        "query_suffix": "practitioners experience real world lessons learned",
        "tavily_params": {
            "search_depth": "advanced",
            "max_results": 10,
        },
        "firecrawl_params": {"limit": 8},
    },
    "contrarian": {
        "client": "both",
        "query_suffix": "criticism problems risks downsides wrong misconceptions controversy",
        "tavily_params": {"search_depth": "advanced", "max_results": 8},
        "firecrawl_params": {"limit": 8},
    },
    "regulatory": {
        "client": "firecrawl",
        "query_suffix": "official guidelines policy regulation government advisory",
        "firecrawl_params": {"limit": 8},
    },
    "historical": {
        "client": "tavily",
        "query_suffix": "history evolution consensus change decades research timeline",
        "tavily_params": {"search_depth": "advanced", "max_results": 10},
    },
    "risk": {
        "client": "tavily",
        "query_suffix": "risks side effects dangers warnings adverse outcomes",
        "tavily_params": {"search_depth": "advanced", "max_results": 10},
    },
    "opinion": {
        "client": "firecrawl",
        "query_suffix": "community opinion discussion forum debate",
        "firecrawl_params": {"limit": 8},
    },
}

# ── Dynamic tier assignment ──────────────────────────────────────────────────

_PRIMARY = [
    "pubmed", "arxiv", "nature.com", "nejm.org", "thelancet.com",
    "who.int", "cdc.gov", ".gov", ".edu", "nih.gov", "cochrane.org",
    "science.org", "cell.com", "bmj.com", "jamanetwork.com",
]
_SECONDARY = [
    "reuters.com", "bbc.com", "bbc.co.uk", "ft.com", "apnews.com",
    "wsj.com", "techcrunch.com", "wired.com", "arstechnica.com",
    "theatlantic.com", "economist.com", "nytimes.com", "theguardian.com",
    "bloomberg.com", "hbr.org", "scientificamerican.com",
]


def assign_source_tier(domain: str) -> str:
    """Dynamic tier assignment after retrieval — not before."""
    d = domain.lower()
    if any(s in d for s in _PRIMARY):
        return "primary"
    if any(s in d for s in _SECONDARY):
        return "secondary"
    return "opinion"


# ── Dynamic time range helper ─────────────────────────────────────────────────

def _get_time_range(lookback_hours: int) -> str:
    """Compute Tavily time_range string from a lookback window in hours."""
    if lookback_hours <= 24:
        return "day"
    if lookback_hours <= 168:
        return "week"
    if lookback_hours <= 720:
        return "month"
    return "year"


# ── Private per-client search helpers ────────────────────────────────────────

async def _search_tavily(full_query: str, params: dict) -> list[dict]:
    """Run a Tavily search and return normalised result list."""
    tavily = _get_tavily()
    response = await tavily.search(full_query, **params)
    out: list[dict] = []
    for r in response.get("results", []):
        url = r.get("url", "")
        if not url:
            continue
        content = r.get("raw_content") or r.get("content", "")
        out.append({
            "url": url,
            "title": r.get("title", ""),
            "markdown": content,
            "domain": "",
            "published_date": r.get("published_date", "unknown"),
            "source": "tavily",
        })
    return out


async def _search_firecrawl_sync(full_query: str, params: dict) -> list[dict]:
    """Run a Firecrawl search (sync wrapped in thread) and return normalised results."""
    fc_results = await asyncio.to_thread(
        firecrawl_search, full_query, params.get("limit", 5)
    )
    out: list[dict] = []
    for r in fc_results:
        out.append({
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "markdown": r.get("markdown", r.get("description", "")),
            "domain": r.get("domain", ""),
            "published_date": r.get("published_date", "unknown"),
            "source": "firecrawl",
        })
    return out


# ── Core routing function ─────────────────────────────────────────────────────

async def route_and_search(angle: str, query: str, lookback_hours: int = 8760) -> list[dict]:
    """
    Routes query to correct client(s) based on angle and lookback window.
    Returns normalised list of {url, title, markdown, domain, published_date, source}.

    Breaking news (lookback_hours <= 24) + recent_news angle: runs Firecrawl AND
    Tavily in parallel for maximum freshness coverage.
    """
    strategy = ROUTING_TABLE.get(angle, ROUTING_TABLE["recent_news"])
    client = strategy["client"]

    # Build full query — no year strings in query text, recency handled by API time_range params
    base_suffix = strategy["query_suffix"]
    full_query = f"{query} {base_suffix}"

    results: list[dict] = []

    # ── Breaking news fast path: both clients in parallel ────────────────────
    if angle == "recent_news" and lookback_hours <= 24:
        tavily_params = dict(strategy.get("tavily_params", {}))
        tavily_params["time_range"] = _get_time_range(lookback_hours)
        fc_params = strategy.get("firecrawl_params", {})

        logger.info("[source_router] Breaking news mode: running Tavily + Firecrawl in parallel")
        tavily_task = asyncio.create_task(_search_tavily(full_query, tavily_params))
        firecrawl_task = asyncio.create_task(_search_firecrawl_sync(full_query, fc_params))
        gathered = await asyncio.gather(tavily_task, firecrawl_task, return_exceptions=True)

        for task_result in gathered:
            if isinstance(task_result, Exception):
                logger.error("[source_router] Breaking news parallel search error: %s", task_result)
            else:
                results.extend(task_result)

        logger.debug("[source_router] Breaking news parallel(%s): %d combined results", angle, len(results))

    else:
        # ── Standard Tavily path ──────────────────────────────────────────────
        if client in ("tavily", "both"):
            params = dict(strategy.get("tavily_params", {}))
            if angle == "recent_news":
                params["time_range"] = _get_time_range(lookback_hours)
            try:
                tavily_results = await _search_tavily(full_query, params)
                results.extend(tavily_results)
                logger.debug("[source_router] Tavily(%s): %d results", angle, len(tavily_results))
            except Exception as e:
                logger.error("[source_router] Tavily search failed (%s): %s", angle, e)

        # ── Standard Firecrawl path ───────────────────────────────────────────
        if client in ("firecrawl", "both"):
            fc_params = strategy.get("firecrawl_params", {})
            try:
                fc_results = await _search_firecrawl_sync(full_query, fc_params)
                results.extend(fc_results)
                logger.debug("[source_router] Firecrawl(%s): %d results", angle, len(fc_results))
            except Exception as e:
                logger.error("[source_router] Firecrawl search failed (%s): %s", angle, e)

    # ── Deduplicate by URL + fill domain ────────────────────────────────────
    from urllib.parse import urlparse
    seen: set[str] = set()
    unique: list[dict] = []
    for r in results:
        url = r["url"]
        if not url or url in seen:
            continue
        seen.add(url)
        if not r.get("domain"):
            r["domain"] = urlparse(url).netloc.lstrip("www.")
        unique.append(r)

    logger.info("[source_router] angle=%s lookback=%dh → %d unique sources", angle, lookback_hours, len(unique))
    return unique
