"""
Conflict Detector Agent.

Phase 5 fix: Claim clustering at 0.75 similarity groups semantically related facts
across different sub-questions. Unique source count per cluster gates badge assignment:
  >= 3 unique sources → VERIFIED
  == 2 unique sources → PARTIALLY_VERIFIED
  == 1 unique source  → UNVERIFIED
  any source contradicts → CONTESTED (LLM detection, overrides cluster verdict)

The cluster's full source list is passed to synthesis so _enforce_badge_independence
can see all 3+ domain URLs and not downgrade VERIFIED claims.
"""
import asyncio
import json
import os
import time
import logging
from dotenv import load_dotenv
from google import genai as google_genai
from langchain_core.messages import SystemMessage, HumanMessage
from agents.contracts import DedupedFinding, ConflictPair, ConflictReport
from agents.prompts.conflict import CONFLICT_DETECTOR_PROMPT
from services.llm_factory import get_reasoning_llm
from services.factguard import verify_conflict_report, safe_verify
from graph.state import ResearchState

load_dotenv()
logger = logging.getLogger(__name__)

# ── Cluster similarity threshold ──────────────────────────────────────────────
# Lower than dedup (0.92) — we group related facts, not near-duplicates.
CLUSTER_SIMILARITY_THRESHOLD = 0.75
_EMBED_MODEL = os.getenv("GOOGLE_EMBED_MODEL", "gemini-embedding-2-preview")

_cluster_client: google_genai.Client | None = None


def _get_cluster_client() -> google_genai.Client:
    global _cluster_client
    if _cluster_client is None:
        _cluster_client = google_genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    return _cluster_client


async def _embed_for_clustering(texts: list[str]) -> list[list[float]]:
    """Embed claims for cosine similarity clustering (same model as dedup)."""
    client = _get_cluster_client()
    prefixed = [f"task: sentence similarity | query: {t}" for t in texts]
    result = await asyncio.to_thread(
        client.models.embed_content,
        model=_EMBED_MODEL,
        contents=prefixed,
    )
    return [list(e.values) for e in result.embeddings]


def _cosine(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity. N=17, dim=768 → trivially fast."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


async def _build_cluster_verdicts(
    findings: list[DedupedFinding],
    job_id: str,
) -> dict[str, tuple[str, list[str]]]:
    """
    Greedy cosine clustering at CLUSTER_SIMILARITY_THRESHOLD.

    Returns:
      dict[finding_id → (cluster_verdict, sorted_unique_source_urls)]

    cluster_verdict:
      VERIFIED           if unique_source_count >= 3
      PARTIALLY_VERIFIED if unique_source_count == 2
      UNVERIFIED         if unique_source_count == 1

    Falls back to {} (old corroboration_count method) on embedding failure.
    """
    if len(findings) < 2:
        return {}

    claims = [f["claim"] for f in findings]
    try:
        vectors = await _embed_for_clustering(claims)
    except Exception as e:
        logger.warning("[%s] Conflict clustering: embedding failed: %s — falling back", job_id, e)
        return {}

    n = len(findings)
    assigned: list[int] = [-1] * n
    clusters: list[list[int]] = []

    for i in range(n):
        if assigned[i] != -1:
            continue
        cluster = [i]
        assigned[i] = len(clusters)
        for j in range(i + 1, n):
            if assigned[j] != -1:
                continue
            if _cosine(vectors[i], vectors[j]) >= CLUSTER_SIMILARITY_THRESHOLD:
                cluster.append(j)
                assigned[j] = len(clusters)
        clusters.append(cluster)

    result: dict[str, tuple[str, list[str]]] = {}
    counts = {"VERIFIED": 0, "PARTIALLY_VERIFIED": 0, "UNVERIFIED": 0}

    for cluster_indices in clusters:
        # Collect ALL unique URLs across every finding in this cluster
        seen: set[str] = set()
        all_urls: list[str] = []
        for idx in cluster_indices:
            f = findings[idx]
            for url in ([f["primary_source_url"]] + list(f.get("corroboration_sources", []))):
                if url and url not in seen:
                    seen.add(url)
                    all_urls.append(url)

        unique_count = len(all_urls)
        if unique_count >= 3:
            verdict = "VERIFIED"
        elif unique_count == 2:
            verdict = "PARTIALLY_VERIFIED"
        else:
            verdict = "UNVERIFIED"

        counts[verdict] += 1
        for idx in cluster_indices:
            result[findings[idx]["id"]] = (verdict, all_urls)

    logger.info(
        "[%s] Conflict clustering: %d findings → %d clusters "
        "(VERIFIED=%d PV=%d UNVERIFIED=%d threshold=%.2f)",
        job_id, n, len(clusters),
        counts["VERIFIED"], counts["PARTIALLY_VERIFIED"], counts["UNVERIFIED"],
        CLUSTER_SIMILARITY_THRESHOLD,
    )
    return result


def _parse_conflicts(content) -> list[dict]:
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
    try:
        parsed = json.loads(content.strip())
        return parsed.get("conflicts", [])
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(content[start:end + 1]).get("conflicts", [])
            except json.JSONDecodeError:
                pass
    return []


def _verdict(has_conflict: bool, cluster_verdict: str | None, finding: DedupedFinding) -> str:
    """CONTESTED overrides clustering. Clustering overrides old corroboration-count method."""
    if has_conflict:
        return "CONTESTED"
    if cluster_verdict:
        return cluster_verdict
    # Fallback: old corroboration_count method when clustering failed
    return "VERIFIED" if finding["corroboration_count"] >= 3 else "UNVERIFIED"


async def conflict_node(state: ResearchState) -> dict:
    """Detect contradictions between findings; assign VERIFIED/PARTIALLY_VERIFIED/UNVERIFIED."""
    deduped: list[DedupedFinding] = state["deduped_findings"]
    job_id = state["job_id"]
    _start = time.monotonic()

    if not deduped or len(deduped) < 2:
        skip_reason = "no_findings" if not deduped else f"only_{len(deduped)}_finding"
        _duration_ms = int((time.monotonic() - _start) * 1000)
        logger.info(
            "[%s] [AGENT_LOG] agent=conflict input_size=%d output_size=0 "
            "duration_ms=%d status=skipped skip_reason=%s",
            job_id, len(deduped), _duration_ms, skip_reason,
        )
        logger.info(
            "[%s] Conflict: skipped — %s (need >= 2 sources to detect conflicts)",
            job_id, skip_reason,
        )
        return {
            "conflict_reports": [],
            "status": "synthesizing",
            "agent_logs": [f"Conflict: skipped ({skip_reason}) — need >= 2 deduped findings"],
        }

    logger.info("[%s] Conflict: analyzing %d findings", job_id, len(deduped))
    llm = get_reasoning_llm()

    # ── Launch clustering CONCURRENTLY with LLM conflict detection ────────────
    # Clustering runs an embedding API call — no need to block LLM detection on it.
    cluster_task = asyncio.create_task(_build_cluster_verdicts(deduped, job_id))

    # ── Group findings by sub_question_id; LLM detects contradictions ─────────
    groups: dict[int, list[DedupedFinding]] = {}
    for f in deduped:
        groups.setdefault(f["sub_question_id"], []).append(f)

    claim_to_conflicts: dict[str, list[ConflictPair]] = {}

    for sq_id, group in groups.items():
        if len(group) < 2:
            continue

        claims_block = "\n".join(
            f"{i+1}. [ID:{f['id'][:8]}] [{f['source_tier'].upper()}] "
            f'"{f["claim"]}" — {f["primary_source_url"]} '
            f"(corroborated {f['corroboration_count']}x)"
            for i, f in enumerate(group)
        )

        try:
            response = await llm.ainvoke([
                SystemMessage(content=CONFLICT_DETECTOR_PROMPT),
                HumanMessage(content=(
                    f"Sub-question group {sq_id} — {len(group)} claims to analyze:\n\n"
                    f"{claims_block}"
                )),
            ])
            raw_conflicts = _parse_conflicts(response.content)
        except Exception as e:
            logger.error("[%s] Conflict: LLM failed for group %d: %s", job_id, sq_id, e)
            raw_conflicts = []

        for rc in raw_conflicts:
            pair = ConflictPair(
                claim_a=rc.get("claim_a", ""),
                source_a=rc.get("source_a", ""),
                claim_b=rc.get("claim_b", ""),
                source_b=rc.get("source_b", ""),
                conflict_type=rc.get("conflict_type", "direct_contradiction"),
                explanation=rc.get("explanation", ""),
            )
            for key in (pair["claim_a"], pair["claim_b"]):
                if key:
                    claim_to_conflicts.setdefault(key, []).append(pair)

    # ── Await cluster verdicts (should be done by now) ────────────────────────
    cluster_verdicts = await cluster_task

    # ── Assign badges using cluster verdicts + CONTESTED from LLM ─────────────
    reports: list[ConflictReport] = []
    counts = {"VERIFIED": 0, "PARTIALLY_VERIFIED": 0, "CONTESTED": 0, "UNVERIFIED": 0}

    for finding in deduped:
        conflicts = claim_to_conflicts.get(finding["claim"], [])
        cv_entry = cluster_verdicts.get(finding["id"])

        if cv_entry:
            cluster_v, cluster_sources = cv_entry
        else:
            # Clustering failed — fallback: use finding's own corroboration data
            cluster_v = None
            cluster_sources = (
                [finding["primary_source_url"]] + list(finding.get("corroboration_sources", []))
            )

        verdict = _verdict(bool(conflicts), cluster_v, finding)
        counts[verdict] += 1

        report = ConflictReport(
            finding_id=finding["id"],
            claim=finding["claim"],
            verdict=verdict,
            confidence_score=finding["confidence_score"],
            primary_source=finding["primary_source_url"],
            corroboration_sources=cluster_sources,
            conflicts=conflicts,
        )
        report = safe_verify(report, verify_conflict_report)
        reports.append(report)

    _duration_ms = int((time.monotonic() - _start) * 1000)
    if _duration_ms < 100:
        logger.warning(
            "[%s] [AGENT_LOG] agent=conflict duration_ms=%d input_size=%d — "
            "suspiciously fast, possible silent skip",
            job_id, _duration_ms, len(deduped),
        )
    logger.info(
        "[%s] Conflict: V=%d PV=%d CONTESTED=%d U=%d",
        job_id, counts["VERIFIED"], counts["PARTIALLY_VERIFIED"],
        counts["CONTESTED"], counts["UNVERIFIED"],
    )
    logger.info(
        "[%s] [AGENT_LOG] agent=conflict input_size=%d output_size=%d "
        "duration_ms=%d status=success",
        job_id, len(deduped), len(reports), _duration_ms,
    )
    return {
        "conflict_reports": reports,
        "status": "synthesizing",
        "agent_logs": [
            f"Conflict: {len(reports)} claims — "
            f"V={counts['VERIFIED']} PV={counts['PARTIALLY_VERIFIED']} "
            f"CONTESTED={counts['CONTESTED']} U={counts['UNVERIFIED']}"
        ],
    }
