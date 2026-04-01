import os
import logging
from urllib.parse import urlparse
from dotenv import load_dotenv
from firecrawl import FirecrawlApp
from firecrawl.v2.types import ScrapeOptions

load_dotenv()
logger = logging.getLogger(__name__)

_app: FirecrawlApp | None = None


def get_client() -> FirecrawlApp:
    global _app
    if _app is None:
        _app = FirecrawlApp(api_key=os.environ["FIRECRAWL_API_KEY"])
    return _app


def search(query: str, limit: int = 5, category: str = "web") -> list[dict]:
    """
    Search via Firecrawl v2 API and return pages with markdown content.

    category: "web" | "news"
    """
    app = get_client()

    kwargs = dict(
        limit=limit,
        scrape_options=ScrapeOptions(
            formats=["markdown"],
            only_main_content=True,
            remove_base64_images=True,
        ),
    )

    try:
        response = app.search(query, **kwargs)
    except Exception as e:
        logger.error("Firecrawl search failed for query '%s': %s", query, e)
        return []

    # v2 API: response is SearchData with .web / .news lists
    raw_list = []
    if response is None:
        return []

    if hasattr(response, "web") and response.web:
        raw_list = response.web
    elif hasattr(response, "data") and response.data:
        # v1 fallback
        raw_list = response.data
    else:
        # Last resort — try iterating if it's a plain list
        if isinstance(response, list):
            raw_list = response

    results = []
    for item in raw_list:
        normalized = _normalise(item)
        if normalized:
            results.append(normalized)

    logger.info("Firecrawl: '%s' → %d results (%d with markdown)",
                query[:60], len(raw_list),
                sum(1 for r in results if len(r.get("markdown", "")) > 100))
    return results


def _normalise(result) -> dict | None:
    """Flatten any Firecrawl result object into a plain dict."""
    url = ""
    title = ""
    markdown = ""
    published_date = "unknown"

    if isinstance(result, dict):
        url = result.get("url", "") or result.get("metadata", {}).get("url", "")
        title = result.get("title", "") or result.get("metadata", {}).get("title", "")
        markdown = result.get("markdown", "") or result.get("content", "") or ""
        meta = result.get("metadata", {}) or {}
        published_date = (
            meta.get("published_time")
            or meta.get("publishedTime")
            or meta.get("dc_date")
            or "unknown"
        )
    else:
        # SDK object — Document has .markdown + .metadata; SearchResultWeb has .url/.title
        raw_md = getattr(result, "markdown", None)
        markdown = raw_md or ""

        metadata = getattr(result, "metadata", None)
        if metadata:
            url = (
                getattr(metadata, "url", "")
                or getattr(metadata, "source_url", "")
                or getattr(metadata, "og_url", "")
                or ""
            )
            title = getattr(metadata, "title", "") or getattr(metadata, "og_title", "") or ""
            published_date = (
                getattr(metadata, "published_time", "")
                or getattr(metadata, "dc_date", "")
                or "unknown"
            ) or "unknown"

        # SearchResultWeb: url/title at top level
        if not url:
            url = getattr(result, "url", "") or ""
        if not title:
            title = getattr(result, "title", "") or ""
        # Use description as fallback markdown for search result snippets
        if not markdown:
            markdown = getattr(result, "description", "") or ""

    if not url:
        return None

    domain = urlparse(url).netloc.lstrip("www.")
    return {
        "url": url,
        "title": title,
        "markdown": markdown,
        "domain": domain,
        "published_date": published_date or "unknown",
    }
