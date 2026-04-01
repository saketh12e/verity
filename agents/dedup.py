import asyncio
import os
import re
import time
import logging
from dotenv import load_dotenv
from google import genai as google_genai
from agents.contracts import CrawlerFinding, DedupedFinding
from agents.citation_graph_agent import run_citation_graph_agent
from services import pinecone_client
from services.citation_graph import CitationGraph
from graph.state import ResearchState

_URL_RE = re.compile(r'https?://[^\s\'"<>\)]{15,}')

load_dotenv()
logger = logging.getLogger(__name__)


class DedupEmptyInputError(Exception):
    """Raised when dedup_agent receives zero findings after crawler merge."""
    pass

SIMILARITY_THRESHOLD = 0.92
EMBED_MODEL = os.getenv("GOOGLE_EMBED_MODEL", "gemini-embedding-2-preview")

_client: google_genai.Client | None = None


def _get_client() -> google_genai.Client:
    global _client
    if _client is None:
        _client = google_genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    return _client


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed texts using gemini-embedding-2-preview.
    gemini-embedding-2-preview uses task prefixes in the prompt (not task_type param).
    For dedup we use sentence similarity — both query and doc get the same prefix.
    """
    client = _get_client()
    # Task prefix format for gemini-embedding-2-preview
    prefixed = [f"task: sentence similarity | query: {t}" for t in texts]

    result = await asyncio.to_thread(
        client.models.embed_content,
        model=EMBED_MODEL,
        contents=prefixed,
    )
    return [list(e.values) for e in result.embeddings]


def _confidence_score(corroboration_count: int, source_tier: str) -> float:
    base = {1: 0.20, 2: 0.45, 3: 0.65, 4: 0.75}.get(corroboration_count, 0.85)
    tier_boost = {"primary": 0.15, "secondary": 0.08, "opinion": 0.0}
    return min(base + tier_boost.get(source_tier, 0.0), 1.0)


async def dedup_node(state: ResearchState) -> dict:
    """Embed claims, upsert to Pinecone, and merge near-duplicate findings."""
    raw_findings: list[CrawlerFinding] = state["raw_findings"]
    job_id = state["job_id"]

    _empty_graph = {"nodes": [], "edges": [], "loops": [], "stats": {}}

    _start = time.monotonic()

    if not raw_findings:
        # Explicit failure — not a silent skip. Crawlers should always produce findings
        # after Phase 1 fixes. If this fires, it's a bug upstream worth surfacing.
        logger.error(
            "[%s] [AGENT_LOG] agent=dedup input_size=0 output_size=0 duration_ms=0 "
            "status=skipped skip_reason=DedupEmptyInputError",
            job_id,
        )
        raise DedupEmptyInputError(
            f"[{job_id}] dedup_agent received 0 findings from crawler merge. "
            "Check crawler logs for failures."
        )

    # ── URL dedup (instant, O(n)) — run before any embedding ──────────────
    seen_urls: set[str] = set()
    url_deduped: list[CrawlerFinding] = []
    for f in raw_findings:
        url = f["source_url"]
        if url not in seen_urls:
            seen_urls.add(url)
            url_deduped.append(f)
        else:
            logger.debug("[%s] Dedup: URL duplicate removed: %s", job_id, url)
    logger.info("[%s] Dedup: URL dedup: %d → %d", job_id, len(raw_findings), len(url_deduped))
    raw_findings = url_deduped

    logger.info("[%s] Dedup: processing %d raw findings with %s", job_id, len(raw_findings), EMBED_MODEL)
    namespace = job_id

    # Step 1: Embed all claims in one batch
    claims = [f["claim"] for f in raw_findings]
    try:
        vectors = await _embed_texts(claims)
    except Exception as e:
        logger.error("[%s] Dedup: embedding failed: %s", job_id, e)
        # Fallback: return all findings as-is without dedup
        deduped = [
            DedupedFinding(
                id=f["id"],
                claim=f["claim"],
                primary_source_url=f["source_url"],
                corroboration_sources=[],
                corroboration_count=1,
                confidence_score=_confidence_score(1, f["source_tier"]),
                source_tier=f["source_tier"],
                publication_date=f["publication_date"],
                sub_question_id=f["sub_question_id"],
            )
            for f in raw_findings
        ]
        return {
            "deduped_findings": deduped,
            "citation_graph_json": _empty_graph,
            "status": "analyzing",
            "agent_logs": [f"Dedup: embedding failed ({e}), returning {len(deduped)} undeduped findings"],
        }

    # Step 2: Upsert all to Pinecone in one batch
    batch = [
        {
            "id": finding["id"],
            "values": vector,
            "metadata": {
                "claim": finding["claim"][:512],
                "source_url": finding["source_url"],
                "source_tier": finding["source_tier"],
                "sub_question_id": finding["sub_question_id"],
            },
        }
        for finding, vector in zip(raw_findings, vectors)
    ]
    await asyncio.to_thread(pinecone_client.upsert_batch, batch, namespace)

    # Step 3: For each finding, query for similar — merge if similarity > threshold
    merged: dict[str, DedupedFinding] = {}   # canonical_id → DedupedFinding
    finding_to_canonical: dict[str, str] = {}  # finding_id → canonical_id

    for finding, vector in zip(raw_findings, vectors):
        fid = finding["id"]

        # Check if already merged into a canonical
        if fid in finding_to_canonical:
            canonical_id = finding_to_canonical[fid]
            if finding["source_url"] not in merged[canonical_id]["corroboration_sources"]:
                merged[canonical_id]["corroboration_sources"].append(finding["source_url"])
                merged[canonical_id]["corroboration_count"] += 1
            continue

        # Query Pinecone for similar
        matches = await asyncio.to_thread(
            pinecone_client.query_similar, vector, 5, namespace
        )

        similar_ids = [
            m["id"] for m in matches
            if m["id"] != fid and m.get("score", 0) >= SIMILARITY_THRESHOLD
        ]

        # Check if any similar finding is already a canonical
        existing_canonical = next(
            (finding_to_canonical[sid] for sid in similar_ids if sid in finding_to_canonical),
            None,
        )

        if existing_canonical:
            canonical_id = existing_canonical
            finding_to_canonical[fid] = canonical_id
            if finding["source_url"] not in merged[canonical_id]["corroboration_sources"]:
                merged[canonical_id]["corroboration_sources"].append(finding["source_url"])
            merged[canonical_id]["corroboration_count"] = len(merged[canonical_id]["corroboration_sources"]) + 1
        else:
            canonical_id = fid
            finding_to_canonical[fid] = canonical_id
            corroboration = [
                raw_findings[i]["source_url"]
                for i, f in enumerate(raw_findings)
                if f["id"] in similar_ids
            ]
            merged[canonical_id] = DedupedFinding(
                id=canonical_id,
                claim=finding["claim"],
                primary_source_url=finding["source_url"],
                corroboration_sources=corroboration,
                corroboration_count=1 + len(corroboration),
                confidence_score=_confidence_score(1 + len(corroboration), finding["source_tier"]),
                source_tier=finding["source_tier"],
                publication_date=finding["publication_date"],
                sub_question_id=finding["sub_question_id"],
            )
            for sid in similar_ids:
                finding_to_canonical[sid] = canonical_id

    deduped = list(merged.values())

    # Recalculate confidence scores with final corroboration counts
    for d in deduped:
        d["confidence_score"] = _confidence_score(d["corroboration_count"], d["source_tier"])

    # Build citation graph from all raw findings.
    # Extract cross-source citation links by scanning raw_excerpts for URLs that
    # appear as source URLs of OTHER findings — these are real cross-citations.
    known_source_urls = {f["source_url"] for f in raw_findings}
    raw_links: dict[str, list[str]] = {}
    for finding in raw_findings:
        excerpt_text = finding.get("raw_excerpt", "")
        found_urls = _URL_RE.findall(excerpt_text)
        # Only track links to OTHER known sources (actual cross-citations, not self-links)
        cross_citations = [
            u for u in found_urls
            if u != finding["source_url"] and u in known_source_urls
        ]
        if cross_citations:
            raw_links[finding["source_url"]] = cross_citations

    cg = CitationGraph()
    try:
        run_citation_graph_agent(raw_findings, cg, raw_links)
        citation_graph_json = cg.to_json()
        logger.info("[%s] Citation graph: %d nodes, %d edges, %d loops",
                    job_id, len(cg.graph.nodes()), len(cg.graph.edges()),
                    len(cg.detect_circular_citations()))
    except Exception as e:
        logger.warning("[%s] Citation graph build failed: %s", job_id, e)
        citation_graph_json = _empty_graph

    _duration_ms = int((time.monotonic() - _start) * 1000)
    if len(raw_findings) > 0 and _duration_ms < 100:
        logger.warning(
            "[%s] [AGENT_LOG] agent=dedup duration_ms=%d input_size=%d — suspiciously fast, possible skip bug",
            job_id, _duration_ms, len(raw_findings),
        )
    logger.info(
        "[%s] [AGENT_LOG] agent=dedup input_size=%d output_size=%d duration_ms=%d status=success",
        job_id, len(raw_findings), len(deduped), _duration_ms,
    )
    logger.info("[%s] Dedup: %d → %d after dedup, %d citation nodes",
                job_id, len(raw_findings), len(deduped), len(cg.graph.nodes()))
    return {
        "deduped_findings": deduped,
        "citation_graph_json": citation_graph_json,
        "status": "analyzing",
        "agent_logs": [
            f"Dedup: reduced {len(raw_findings)} raw findings → {len(deduped)} unique claims "
            f"(threshold: {SIMILARITY_THRESHOLD}, model: {EMBED_MODEL})"
        ],
    }
