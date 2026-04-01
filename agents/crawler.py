"""
Crawler Agent — V3.
One LLM call for query optimization + ONE batched LLM call for all claim extraction.
Reduces per-crawler API calls from N (one per page) to exactly 2.
Total across 4 parallel crawlers: 8 LLM calls instead of 60+.

V3: Domain diversity enforcement — max 2 sources per root domain,
prioritizing high-credibility sources (academic, gov, industry press).
"""
import asyncio
import json
import re
import uuid
import logging
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage
from agents.contracts import CrawlerFinding, SubQuestion
from agents.prompts.crawler import SEARCH_OPTIMIZER_PROMPT, CLAIM_EXTRACTOR_PROMPT
from services.llm_factory import get_fast_llm
from services.source_router import route_and_search, assign_source_tier
from services.factguard import verify_crawler_finding, safe_verify
from graph.state import ResearchState

load_dotenv()
logger = logging.getLogger(__name__)

CURRENT_YEAR = datetime.now().year
YEAR_RANGE = f"{CURRENT_YEAR - 3}–{CURRENT_YEAR}"

# Domain diversity: max sources per unique root domain
MAX_SOURCES_PER_DOMAIN = 2

# Tier priority for diversity enforcement (lower = higher priority)
_TIER_PRIORITY = {"primary": 0, "secondary": 1, "opinion": 2}

# Per-page content cap — 2500 chars gives ~3–4 paragraphs per source.
# At 12 sources × 2500 chars ≈ 30k chars → ~7-8k tokens — well within fast model context.
# 800 was too low: barely 2 sentences, LLM couldn't extract meaningful claims.
PAGE_CONTENT_CAP = 2500


def _extract_text(content) -> str:
    """Normalize Gemini response content — may be str or list of parts."""
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return content


def _parse_claims(content) -> list[dict]:
    """Robustly parse JSON array of claims from LLM response."""
    content = _extract_text(content)
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
    content = content.strip()

    try:
        data = json.loads(content)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "claims" in data:
            return data["claims"]
        return []
    except json.JSONDecodeError:
        pass

    # Fallback: find first [...] block
    start = content.find("[")
    end = content.rfind("]")
    if start != -1 and end != -1:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse claims JSON: %s", content[:300])
    return []


def _enforce_domain_diversity(pages: list[dict], job_id: str, sq_id: int) -> list[dict]:
    """Enforce max sources per domain, prioritizing higher tier sources."""
    domain_map: dict[str, list[dict]] = {}
    for p in pages:
        domain = p.get("domain") or urlparse(p.get("url", "")).netloc.lstrip("www.")
        if domain not in domain_map:
            domain_map[domain] = []
        domain_map[domain].append(p)

    final_pages = []
    for domain, domain_pages in domain_map.items():
        # Sort by tier priority (0 is best)
        sorted_pages = sorted(
            domain_pages,
            key=lambda x: _TIER_PRIORITY.get(assign_source_tier(domain), 3)
        )
        kept = sorted_pages[:MAX_SOURCES_PER_DOMAIN]
        final_pages.extend(kept)
        if len(domain_pages) > MAX_SOURCES_PER_DOMAIN:
            logger.info(
                "[%s] Crawler %d: diversity cap applied to %s (kept %d/%d)",
                job_id, sq_id, domain, len(kept), len(domain_pages)
            )
    return final_pages


async def crawler_node(state: ResearchState) -> dict:
    """
    Search for one sub-question and extract atomic claims.
    Makes exactly 2 LLM calls per crawler:
      Call 1: generate 3 query variations (optimizer)
      Call 2: batched claim extraction from ALL pages at once
    All 3 query variations run in parallel via route_and_search.
    """
    sub_question: SubQuestion = state["sub_question"]
    query = state["query"]
    job_id = state["job_id"]

    sq_id = sub_question["id"]
    angle = sub_question["angle"]
    question = sub_question["question"]

    # Pick up lookback_hours from state — defaults to 8760 (deep research) if not set
    lookback_hours: int = state.get("lookback_hours", 8760)

    logger.info("[%s] Crawler %d (%s): %s (lookback=%dh)", job_id, sq_id, angle, question[:80], lookback_hours)

    llm = get_fast_llm()

    # ── LLM Call 1: generate 3 query variations for maximum source coverage ─
    # Returns a JSON array of 3 search strings differing in specificity.
    search_queries: list[str] = []
    try:
        opt_response = await llm.ainvoke([
            SystemMessage(content=SEARCH_OPTIMIZER_PROMPT),
            HumanMessage(content=(
                f"Research question: {question}\n"
                f"Angle: {angle}\n"
                f"Current year: {CURRENT_YEAR}"
            )),
        ])
        raw_opt = _extract_text(opt_response.content).strip()
        # Try to parse as JSON array of query strings
        parsed_queries = _parse_claims(raw_opt)  # reuse JSON parser
        if parsed_queries and isinstance(parsed_queries[0], str):
            search_queries = [q.strip().strip('"').strip("'") for q in parsed_queries if q.strip()]
        elif parsed_queries and isinstance(parsed_queries[0], dict):
            # Model wrapped each in {"query": "..."} — extract
            search_queries = [
                (q.get("query") or q.get("variation") or "").strip()
                for q in parsed_queries if isinstance(q, dict)
            ]
            search_queries = [q for q in search_queries if q]
        if not search_queries:
            # Fallback: treat entire response as a single query
            search_queries = [raw_opt.split("\n")[0].strip().strip('"')]
    except Exception as e:
        logger.error("[%s] Crawler %d: query optimization failed: %s", job_id, sq_id, e)

    # Always have at least the raw question as fallback
    if not search_queries:
        search_queries = [question[:100]]
    # Cap at 3 variations, deduplicate
    search_queries = list(dict.fromkeys(q for q in search_queries if q))[:3]

    primary_query = search_queries[0]
    logger.info("[%s] Crawler %d: %d query variations: %s", job_id, sq_id, len(search_queries), search_queries)

    # ── Fetch pages for all query variations in parallel (45s total timeout) ─
    async def _fetch_one(q: str) -> list[dict]:
        return await route_and_search(angle, q, lookback_hours=lookback_hours)

    try:
        all_results = await asyncio.wait_for(
            asyncio.gather(*[_fetch_one(q) for q in search_queries], return_exceptions=True),
            timeout=45.0,
        )
    except asyncio.TimeoutError:
        logger.warning("[%s] Crawler %d: parallel fetch timeout after 45s", job_id, sq_id)
        all_results = []

    # Merge unique pages across all query results
    seen_urls: set[str] = set()
    pages: list[dict] = []
    for result in all_results:
        if isinstance(result, Exception):
            logger.error("[%s] Crawler %d: query variation fetch error: %s", job_id, sq_id, result)
            continue
        for page in result:
            url = page.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                pages.append(page)

    logger.info("[%s] Crawler %d: %d unique pages from %d query variations", job_id, sq_id, len(pages), len(search_queries))

    if not pages:
        return {
            "raw_findings": [],
            "agent_logs": [f"Crawler [{angle}]: {search_queries} — no pages returned"],
        }

    # Filter out empty pages
    valid_pages = [p for p in pages if p.get("url") and len(p.get("markdown", "")) >= 50]
    if not valid_pages:
        return {
            "raw_findings": [],
            "agent_logs": [f"Crawler [{angle}]: {search_queries} — no valid page content"],
        }

    # ── Enforce domain diversity: max MAX_SOURCES_PER_DOMAIN per root domain ─
    valid_pages = _enforce_domain_diversity(valid_pages, job_id, sq_id)

    # ── Retry with broadened query if fewer than 4 sources found ───────────
    if len(valid_pages) < 4:
        # Broaden: drop the most specific term from primary query
        broadened = re.sub(r'\s+\S+$', '', primary_query).strip() or primary_query[:60]
        logger.warning(
            "[%s] Crawler %d: only %d sources found, retrying with broadened query: '%s'",
            job_id, sq_id, len(valid_pages), broadened,
        )
        try:
            retry_pages = await asyncio.wait_for(
                route_and_search(angle, broadened, lookback_hours=lookback_hours),
                timeout=45.0,
            )
            existing_urls = {p["url"] for p in valid_pages}
            new_pages = [
                p for p in retry_pages
                if p.get("url") and p["url"] not in existing_urls
                and len(p.get("markdown", "")) >= 50
            ]
            valid_pages = valid_pages + new_pages
            logger.info(
                "[%s] Crawler %d: retry added %d pages, total now %d",
                job_id, sq_id, len(new_pages), len(valid_pages),
            )
        except Exception as e:
            logger.error("[%s] Crawler %d: retry failed: %s", job_id, sq_id, e)

    if len(valid_pages) < 2:
        logger.error(
            "[%s] Crawler %d (%s): insufficient sources after retry. primary_query='%s' count=%d",
            job_id, sq_id, angle, primary_query, len(valid_pages),
        )

    # ── Build batched prompt with ALL pages (capped per page) ──────────────
    # One combined prompt → one LLM call → all claims across all sources
    sources_block_parts = []
    url_to_meta: dict[str, dict] = {}   # url → {domain, pub_date, markdown}

    for i, page in enumerate(valid_pages):
        url = page["url"]
        markdown = page.get("markdown", "")
        domain = page.get("domain", "") or urlparse(url).netloc.lstrip("www.")
        pub_date = page.get("published_date", "unknown")

        # Cap per-page content to avoid token overflow
        content_excerpt = markdown[:PAGE_CONTENT_CAP]

        url_to_meta[url] = {
            "domain": domain,
            "pub_date": pub_date,
            "markdown": markdown,  # keep full for citation graph URL extraction
        }

        sources_block_parts.append(
            f"--- SOURCE {i + 1} ---\n"
            f"URL: {url}\n"
            f"Domain: {domain}\n"
            f"Published: {pub_date}\n"
            f"Content:\n{content_excerpt}"
        )

    sources_block = "\n\n".join(sources_block_parts)

    # ── LLM Call 2: extract ALL claims from ALL sources in one shot ────────
    try:
        extract_response = await llm.ainvoke([
            SystemMessage(content=CLAIM_EXTRACTOR_PROMPT),
            HumanMessage(content=(
                f"Research question: {question}\n"
                f"Angle: {angle}\n\n"
                f"SOURCES ({len(valid_pages)} total):\n\n"
                f"{sources_block}"
            )),
        ])
        raw_claims = _parse_claims(extract_response.content)
        logger.info(
            "[%s] Crawler %d (%s): %d raw claims from %d pages (1 LLM call)",
            job_id, sq_id, angle, len(raw_claims), len(valid_pages),
        )
    except Exception as e:
        logger.error("[%s] Crawler %d: batch extraction failed: %s", job_id, sq_id, e)
        raw_claims = []

    # ── Process claims and build CrawlerFindings ───────────────────────────
    findings: list[CrawlerFinding] = []

    # Track per-URL claim count to cap 5 per source
    claims_per_url: dict[str, int] = {}

    for item in raw_claims:
        claim_text = item.get("claim", "").strip()
        source_url = item.get("source_url", "").strip()

        if not claim_text or len(claim_text) < 20:
            continue

        # Match claim to a known source URL
        if source_url not in url_to_meta:
            # Try to match a partial URL
            source_url = next(
                (u for u in url_to_meta if source_url in u or u in source_url),
                None,
            )
        if not source_url:
            continue  # Can't attribute — skip

        if claims_per_url.get(source_url, 0) >= 5:
            continue  # Cap 5 per source

        meta = url_to_meta[source_url]
        domain = meta["domain"]
        pub_date = meta["pub_date"]
        tier = assign_source_tier(domain)

        # Use provided raw_excerpt or fall back to first 200 chars of content
        raw_excerpt = item.get("raw_excerpt", "")
        if len(raw_excerpt) < 20:
            raw_excerpt = meta["markdown"][:200]

        finding = CrawlerFinding(
            id=str(uuid.uuid4()),
            claim=claim_text,
            source_url=source_url,
            source_domain=domain,
            source_tier=tier,
            publication_date=pub_date,
            sub_question_id=sq_id,
            raw_excerpt=raw_excerpt[:600],
        )

        verified = safe_verify(finding, verify_crawler_finding)
        if verified.get("factguard_flagged"):
            logger.debug(
                "[%s] Crawler %d: FactGuard flagged claim from %s: %s",
                job_id, sq_id, domain, verified.get("factguard_reason"),
            )

        findings.append(verified)
        claims_per_url[source_url] = claims_per_url.get(source_url, 0) + 1

    logger.info(
        "[%s] Crawler %d (%s): %d findings from %d pages",
        job_id, sq_id, angle, len(findings), len(valid_pages),
    )
    # Structured log — matches spec Section 6 format
    logger.info(
        "[%s] [AGENT_LOG] agent=crawler_%d provider=multi query_variations=%d "
        "sources_fetched=%d sources_valid=%d findings=%d angle=%s primary_query='%s'",
        job_id, sq_id, len(search_queries),
        len(pages), len(valid_pages), len(findings), angle, primary_query,
    )

    return {
        "raw_findings": findings,
        "agent_logs": [
            f"Crawler [{angle}]: {len(search_queries)} queries → "
            f"{len(findings)} claims from {len(valid_pages)} pages"
        ],
    }
