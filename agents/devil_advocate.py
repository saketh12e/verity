"""
Devil's Advocate Agent — Phase 4.
Receives batched VERIFIED claims (max 5) via single Send() from route_to_devil_advocate.

Per-claim flow (sequential with 2s inter-claim sleep):
  1. Check semantic cache — if cosine similarity >0.85 with previous result, reuse
  2. Generate 2 adversarial Tavily queries (challenge framing + alternative explanation)
  3. Run both queries in parallel via Tavily (10s timeout each, basic/news)
  4. LLM analyzes results and produces structured challenge object
  5. Apply challenge: downgrade verdict if strong/moderate, leave unchanged if weak/none
  6. Return updated conflict_reports + per-claim devil_advocate_metadata

Hard timeout: 20 seconds per claim. Never blocks the pipeline.
"""
import os
import json
import asyncio
import logging
import time
import traceback
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage
from tavily import AsyncTavilyClient
from agents.contracts import ConflictReport, ConflictPair
from agents.prompts.devil_advocate import ADVERSARIAL_QUERY_PROMPT, DEVIL_ADVOCATE_SYSTEM
from services.llm_factory import get_reasoning_llm
from graph.state import ResearchState

load_dotenv()
logger = logging.getLogger(__name__)

_tavily: AsyncTavilyClient | None = None


def _get_tavily() -> AsyncTavilyClient:
    global _tavily
    if _tavily is None:
        _tavily = AsyncTavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _tavily


def _parse_json(content) -> dict:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {}


# ── A4: Semantic cache helpers ────────────────────────────────────────────────

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _simple_embedding(text: str) -> list[float]:
    """
    Lightweight character-trigram frequency embedding for semantic cache.
    Not using external APIs — avoids extra network calls while providing
    reasonable similarity detection for near-duplicate claims.
    """
    text = text.lower().strip()
    # Build trigram frequency vector using a fixed hash space
    dim = 256
    vec = [0.0] * dim
    for i in range(len(text) - 2):
        trigram = text[i:i + 3]
        idx = hash(trigram) % dim
        vec[idx] += 1.0
    # Normalize
    total = sum(v * v for v in vec) ** 0.5
    if total > 0:
        vec = [v / total for v in vec]
    return vec


async def _generate_adversarial_queries(claim: str, job_id: str) -> tuple[str, str]:
    """
    Generate 2 adversarial search queries for a VERIFIED claim.
    Returns (challenge_query, alternative_query).
    Falls back to deterministic templates if LLM call fails.
    """
    llm = get_reasoning_llm()
    try:
        response = await llm.ainvoke([
            SystemMessage(content=ADVERSARIAL_QUERY_PROMPT),
            HumanMessage(content=f"VERIFIED claim: {claim}"),
        ])
        parsed = _parse_json(response.content)
        q1 = parsed.get("challenge_query", "").strip()
        q2 = parsed.get("alternative_query", "").strip()
        if q1 and q2:
            logger.debug("[%s] DA: generated queries — challenge: %.80s", job_id, q1)
            return q1, q2
    except Exception as e:
        logger.warning("[%s] DA: query generation LLM failed: %s — using fallback templates", job_id, e)

    # Deterministic fallback templates
    short = claim[:120]
    q1 = f'"{short}" denied OR refuted OR incorrect OR false OR rebuttal'
    q2 = f'alternative explanation "{short[:80]}" different cause OR interpretation'
    return q1, q2


async def _tavily_search(query: str, job_id: str) -> list[dict]:
    """
    Run one Tavily search with 10s hard timeout (A5).
    Uses search_depth="basic" and topic="news" for speed.
    Returns list of result dicts. Returns [] on timeout or error — never blocks.
    """
    try:
        client = _get_tavily()
        response = await asyncio.wait_for(
            client.search(
                query,
                search_depth="basic",     # A5: basic instead of advanced
                topic="news",
                max_results=5,
            ),
            timeout=10.0,                  # A5: 10s instead of 20s
        )
        results = response.get("results", [])
        logger.debug("[%s] DA: Tavily returned %d results for query: %.60s", job_id, len(results), query)
        return results
    except asyncio.TimeoutError:
        logger.warning("[%s] DA: Tavily query timed out (10s): %.80s", job_id, query)
        return []
    except Exception as e:
        logger.warning("[%s] DA: Tavily search failed: %s", job_id, e)
        return []


def _format_results(results: list[dict]) -> str:
    if not results:
        return "No results found."
    parts = []
    for r in results:
        content = (r.get("content") or r.get("snippet", ""))[:500]
        parts.append(
            f"Title: {r.get('title', 'unknown')}\n"
            f"URL: {r.get('url', '')}\n"
            f"Content: {content}"
        )
    return "\n\n---\n\n".join(parts)


async def _generate_challenge(
    claim: str,
    source: str,
    q_challenge: str,
    q_alternative: str,
    results_challenge: list[dict],
    results_alternative: list[dict],
    job_id: str,
) -> dict:
    """
    Use LLM to analyze Tavily results and produce a structured challenge object.
    Returns the parsed challenge dict. Falls back to challenge_found=False on error.
    """
    llm = get_reasoning_llm()
    try:
        response = await llm.ainvoke([
            SystemMessage(content=DEVIL_ADVOCATE_SYSTEM),
            HumanMessage(content=(
                f"VERIFIED claim to challenge:\n\"{claim}\"\n"
                f"Original source: {source}\n\n"
                f"## Tavily Search Results — Challenge Framing\n"
                f"Query: {q_challenge}\n\n"
                f"{_format_results(results_challenge)}\n\n"
                f"## Tavily Search Results — Alternative Explanation Framing\n"
                f"Query: {q_alternative}\n\n"
                f"{_format_results(results_alternative)}\n\n"
                f"Analyze these results and produce the challenge object JSON."
            )),
        ])
        parsed = _parse_json(response.content)
        if parsed:
            return parsed
    except Exception as e:
        logger.error("[%s] DA: challenge generation LLM failed: %s", job_id, e)

    # Safe fallback — treat as no challenge found
    return {
        "original_claim": claim,
        "original_badge": "VERIFIED",
        "challenge_found": False,
        "challenge_strength": "none",
        "counter_evidence": [],
        "recommended_badge_change": "no_change",
        "challenge_note": "",
    }


def _apply_challenge(report: ConflictReport, challenge: dict) -> ConflictReport:
    """
    Mutate conflict_report based on challenge_strength.

    strong / moderate → downgrade verdict VERIFIED → CONTESTED, add ConflictPairs
    weak              → keep verdict, challenge note lives only in metadata
    none              → keep verdict unchanged
    """
    strength = challenge.get("challenge_strength", "none")
    if strength not in ("strong", "moderate"):
        return report  # no structural change — weak/none don't alter the report

    updated = dict(report)
    updated["verdict"] = "CONTESTED"
    challenge_note = challenge.get("challenge_note", "Devil's Advocate challenge found")
    counter_evidence = challenge.get("counter_evidence", [])

    new_pairs = []
    for ce in counter_evidence[:3]:
        url = ce.get("url", "")
        if not url:
            continue
        new_pairs.append(ConflictPair(
            claim_a=report["claim"],
            source_a=report["primary_source"],
            claim_b=ce.get("excerpt", challenge_note),
            source_b=url,
            conflict_type=(
                "direct_contradiction" if strength == "strong" else "scope_difference"
            ),
            explanation=challenge_note,
        ))

    if new_pairs:
        updated["conflicts"] = list(report.get("conflicts", [])) + new_pairs

    return updated


async def _run_da_on_claim(
    target_report: ConflictReport,
    claim_index: int,
    job_id: str,
) -> tuple[ConflictReport, dict]:
    """
    Run the full DA flow on a single claim.
    Returns (updated_report, metadata_dict).
    """
    claim = target_report["claim"]
    source = target_report["primary_source"]
    _start = time.monotonic()

    logger.info("[%s] Devil's Advocate: challenging claim_index %d '%s...'", job_id, claim_index, claim[:70])

    # Step 1: Generate 2 adversarial queries
    q_challenge, q_alternative = await _generate_adversarial_queries(claim, job_id)

    # Step 2: Run both queries in parallel (10s hard timeout each, Tavily only)
    results_challenge, results_alternative = await asyncio.gather(
        _tavily_search(q_challenge, job_id),
        _tavily_search(q_alternative, job_id),
    )

    counter_sources_found = len(results_challenge) + len(results_alternative)
    adversarial_queries_run = 2

    if counter_sources_found == 0:
        logger.warning("[%s] DA: 0 counter-sources found for claim '%.40s...'", job_id, claim)

    # Step 3: LLM generates structured challenge object
    challenge = await _generate_challenge(
        claim, source,
        q_challenge, q_alternative,
        results_challenge, results_alternative,
        job_id,
    )

    challenge_found = challenge.get("challenge_found", False)
    challenge_strength = challenge.get("challenge_strength", "none")
    recommended_change = challenge.get("recommended_badge_change", "no_change")
    downgraded = challenge_strength in ("strong", "moderate")

    # Step 4: Apply challenge → update conflict_report
    updated_report = _apply_challenge(target_report, challenge)

    _duration_ms = int((time.monotonic() - _start) * 1000)

    metadata = {
        "devil_advocate_ran": True,
        "claim_index": claim_index,
        "claims_targeted": 1,
        "adversarial_queries_run": adversarial_queries_run,
        "counter_sources_found": counter_sources_found,
        "challenges_produced": 1 if challenge_found else 0,
        "claims_challenged": 1 if challenge_found else 0,
        "claims_downgraded": 1 if downgraded else 0,
        "claims_with_strong_challenge": 1 if challenge_strength == "strong" else 0,
        "claims_with_no_challenge": 1 if not challenge_found else 0,
        "challenge_strength": challenge_strength,
        "recommended_badge_change": recommended_change,
        "duration_ms": _duration_ms,
        "status": "success",
        "skip_reason": None,
    }

    log_suffix = f" → downgraded to CONTESTED" if downgraded else f" → {challenge_strength}"
    log_entry = f"Devil's Advocate: '{claim[:60]}' — strength={challenge_strength}{log_suffix}"

    logger.info(
        "[%s] DA claim_index %d completed in %dms — strength=%s%s",
        job_id, claim_index, _duration_ms, challenge_strength, log_suffix,
    )

    return updated_report, metadata, log_entry


async def devil_advocate_node(state: ResearchState) -> dict:
    """
    Phase 4 devil's advocate: sequential processing of top 5 VERIFIED claims.

    Fix A2: Sequential loop with 2s inter-claim sleep (no parallel fan-out).
    Fix A3: 20s hard timeout per claim.
    Fix A4: Semantic cache — skip near-duplicate claims (cosine similarity >0.85).
    """
    job_id = state["job_id"]
    _start = time.monotonic()

    # Support both batched (new) and single-target (legacy) modes
    targets = state.get("devil_advocate_targets") or []
    legacy_target = state.get("devil_advocate_target")
    if not targets and legacy_target:
        targets = [legacy_target]

    if not targets:
        return {
            "conflict_reports": [],
            "devil_advocate_metadata": [],
            "agent_logs": [],
        }

    # ── A4: Semantic cache setup ──────────────────────────────────────────
    cache_embeddings: list[list[float]] = []   # embeddings of processed claims
    cache_results: list[tuple] = []             # (updated_report, metadata, log_entry)

    all_updated_reports: list[ConflictReport] = []
    all_metadata: list[dict] = []
    all_logs: list[str] = []

    total_claims = len(targets)
    logger.info("[%s] DA starting sequential processing of %d claims", job_id, total_claims)

    for idx, target_report in enumerate(targets):
        claim = target_report["claim"]

        # ── A4: Check semantic cache ──────────────────────────────────────
        claim_embedding = _simple_embedding(claim)
        cache_hit = False
        for cached_idx, cached_emb in enumerate(cache_embeddings):
            sim = _cosine_similarity(claim_embedding, cached_emb)
            if sim > 0.85:
                logger.info(
                    "[%s] DA cache hit for claim_index %d — reusing result "
                    "(similarity %.3f with cached claim_index %d)",
                    job_id, idx, sim, cached_idx,
                )
                cached_report, cached_meta, cached_log = cache_results[cached_idx]
                # Reuse but update with this claim's actual report data
                reused_report = _apply_challenge(target_report, {
                    "challenge_strength": cached_meta.get("challenge_strength", "none"),
                    "challenge_note": "Reused from near-duplicate claim (DA cache hit)",
                    "counter_evidence": [],
                })
                reused_meta = {**cached_meta, "claim_index": idx, "status": "cache_hit"}
                reused_log = f"Devil's Advocate: '{claim[:60]}' — CACHE HIT (sim={sim:.3f})"
                all_updated_reports.append(reused_report)
                all_metadata.append(reused_meta)
                all_logs.append(reused_log)
                cache_hit = True
                break

        if cache_hit:
            # Still add to cache for future comparisons
            cache_embeddings.append(claim_embedding)
            cache_results.append((reused_report, reused_meta, reused_log))
            # ── A2: 2s sleep between claims ───────────────────────────────
            if idx < total_claims - 1:
                await asyncio.sleep(2)
            continue

        # ── A3: 20s hard timeout per claim ────────────────────────────────
        try:
            result = await asyncio.wait_for(
                _run_da_on_claim(target_report, idx, job_id),
                timeout=20.0,
            )
            updated_report, metadata, log_entry = result
            all_updated_reports.append(updated_report)
            all_metadata.append(metadata)
            all_logs.append(log_entry)

            # Add to semantic cache
            cache_embeddings.append(claim_embedding)
            cache_results.append(result)

        except asyncio.TimeoutError:
            logger.warning("[%s] DA timeout claim_index %d — skipping", job_id, idx)
            # Return the original report unchanged on timeout
            all_updated_reports.append(target_report)
            all_metadata.append({
                "devil_advocate_ran": True,
                "claim_index": idx,
                "claims_targeted": 1,
                "adversarial_queries_run": 0,
                "counter_sources_found": 0,
                "challenges_produced": 0,
                "claims_challenged": 0,
                "claims_downgraded": 0,
                "claims_with_strong_challenge": 0,
                "claims_with_no_challenge": 1,
                "challenge_strength": "none",
                "recommended_badge_change": "no_change",
                "duration_ms": 20000,
                "status": "timeout",
                "skip_reason": "hard_timeout_20s",
            })
            all_logs.append(f"Devil's Advocate: claim_index {idx} — TIMEOUT (20s)")

        except Exception as e:
            logger.error(
                "[%s] DA claim_index %d unexpected error: %s\n%s",
                job_id, idx, e, traceback.format_exc(),
            )
            all_updated_reports.append(target_report)
            all_metadata.append({
                "devil_advocate_ran": True,
                "claim_index": idx,
                "status": "error",
                "skip_reason": str(e),
                "duration_ms": 0,
            })
            all_logs.append(f"Devil's Advocate: claim_index {idx} — ERROR: {e}")

        # ── A2: 2s async sleep between claims ─────────────────────────────
        if idx < total_claims - 1:
            await asyncio.sleep(2)

    _total_duration_ms = int((time.monotonic() - _start) * 1000)

    # ── Structured summary log ────────────────────────────────────────────
    total_challenges = sum(1 for m in all_metadata if m.get("challenges_produced", 0) > 0)
    total_strong = sum(1 for m in all_metadata if m.get("claims_with_strong_challenge", 0) > 0)
    total_no_challenge = sum(1 for m in all_metadata if m.get("claims_with_no_challenge", 0) > 0)
    total_queries = sum(m.get("adversarial_queries_run", 0) for m in all_metadata)
    total_counter = sum(m.get("counter_sources_found", 0) for m in all_metadata)

    logger.info(
        "[%s] [AGENT_LOG] agent=devil_advocate_agent claims_targeted=%d "
        "adversarial_queries_run=%d counter_sources_found=%d challenges_produced=%d "
        "claims_with_strong_challenge=%d claims_with_no_challenge=%d "
        "duration_ms=%d status=success skip_reason=null",
        job_id, total_claims, total_queries, total_counter,
        total_challenges, total_strong, total_no_challenge, _total_duration_ms,
    )

    if _total_duration_ms < 100 and total_claims > 0:
        logger.warning(
            "[%s] DA: completed in <100ms on %d claims — suspect zero-latency bug",
            job_id, total_claims,
        )

    return {
        "conflict_reports": all_updated_reports,
        "devil_advocate_metadata": all_metadata,
        "agent_logs": all_logs,
    }
