import json
import logging
import re
import time
import asyncio
import traceback
from datetime import datetime, timezone
from urllib.parse import urlparse
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage
from agents.contracts import (
    ConflictReport, ResearchReport, ReportSection, ReportClaim,
    CitationNode, CitationEdge, CitationGraphData,
)
from agents.prompts.synthesis import SYNTHESIS_PROMPT
from services.llm_factory import get_reasoning_llm
from services.source_router import assign_source_tier
from graph.state import ResearchState

load_dotenv()
logger = logging.getLogger(__name__)


class SynthesisEmptySourcesError(Exception):
    """Raised when synthesis_agent receives zero conflict reports to synthesize."""
    pass


class SynthesisEmptySectionsError(Exception):
    """Raised when the LLM returns valid JSON but with empty sections."""
    pass


# Badge → hex color (single source of truth — frontend reads node_color directly)
_BADGE_COLORS = {
    "VERIFIED":           "#22c55e",
    "PARTIALLY_VERIFIED": "#eab308",
    "UNVERIFIED":         "#f97316",
    "UNSUPPORTED":        "#ef4444",
}
_GEMINI_GROUNDING_BORDER = "#fbbf24"


def _root_domain(url: str) -> str:
    """Extract root domain for independence checking. 'www.techcrunch.com' → 'techcrunch.com'."""
    try:
        parts = urlparse(url).netloc.lstrip("www.").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else parts[0]
    except Exception:
        return url[:30]


def _enforce_badge_independence(claim: ReportClaim) -> ReportClaim:
    """
    Python-enforced independence rule — LLM badge assignments are audited here.
    VERIFIED requires 3+ supporting sources from 3 different root domains.
    If fewer than 3 unique domains, downgrade to PARTIALLY_VERIFIED.
    """
    if claim.get("badge") != "VERIFIED":
        return claim
    supporting = claim.get("sources", [])
    unique_domains = {_root_domain(u) for u in supporting if u}
    if len(unique_domains) < 3:
        logger.debug(
            "Badge downgrade VERIFIED→PARTIALLY_VERIFIED: only %d unique domains (%s)",
            len(unique_domains), unique_domains,
        )
        claim["badge"] = "PARTIALLY_VERIFIED"
    return claim


def _sanitize_json_string(content: str) -> str:
    """
    Fix common LLM JSON issues: unescaped quotes inside strings,
    trailing commas, etc.
    """
    # Remove any markdown code fences
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
    content = content.strip()

    # Find the JSON object boundaries
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1:
        content = content[start:end + 1]

    # Remove trailing commas before } or ]
    content = re.sub(r',\s*([}\]])', r'\1', content)

    return content


def _parse_report(content) -> dict:
    """Parse JSON from LLM response with robust error recovery."""
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )

    sanitized = _sanitize_json_string(content)

    try:
        parsed = json.loads(sanitized)
    except json.JSONDecodeError:
        # Try to fix unescaped newlines in string values
        fixed = sanitized.replace('\n', '\\n').replace('\r', '\\r')
        # But preserve the JSON structural newlines by un-escaping them
        # around { } [ ] , :
        fixed = re.sub(r'\\n\s*([{\[\]},:])', lambda m: '\n' + m.group(1), fixed)
        fixed = re.sub(r'([{\[\]},:])\s*\\n', lambda m: m.group(1) + '\n', fixed)
        try:
            parsed = json.loads(fixed)
        except json.JSONDecodeError:
            parsed = None

        if parsed is None:
            # Last resort: try extracting just the core fields with regex
            try:
                exec_match = re.search(r'"executive_summary"\s*:\s*"((?:[^"\\]|\\.)*)"', sanitized, re.DOTALL)
                title_match = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', sanitized, re.DOTALL)
                if exec_match:
                    # Partial extraction worked — build a minimal result
                    parsed = {
                        "title": title_match.group(1) if title_match else "Research Report",
                        "executive_summary": exec_match.group(1).replace('\\n', '\n'),
                        "claims": [],
                        "sections": [],
                        "open_questions": [],
                    }
            except Exception:
                pass

        if parsed is None:
            raise json.JSONDecodeError("Could not parse LLM response as JSON", sanitized[:200], 0)

    # ── Rescue sections from truncated JSON ───────────────────────────────
    # If the LLM output was truncated, sections may be empty even though
    # section content exists in the raw text. Try to extract them.
    if not parsed.get("sections") and '"heading"' in sanitized and '"content"' in sanitized:
        logger.debug("Attempting to rescue sections from truncated JSON")
        section_pattern = re.finditer(
            r'\{\s*"heading"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"content"\s*:\s*"((?:[^"\\]|\\.)*)"',
            sanitized,
            re.DOTALL,
        )
        rescued_sections = []
        for m in section_pattern:
            heading = m.group(1).replace('\\n', '\n').strip()
            content = m.group(2).replace('\\n', '\n').strip()
            if heading and content and len(content) > 20:
                rescued_sections.append({
                    "heading": heading,
                    "content": content,
                    "claims_referenced": [],
                    "sources": [],
                })
        if rescued_sections:
            logger.info("Rescued %d sections from truncated JSON", len(rescued_sections))
            parsed["sections"] = rescued_sections

    return parsed


def _build_readable_findings(reports: list[ConflictReport], max_chars: int = 400) -> str:
    """
    Convert conflict reports into readable sentences for the LLM.
    Not raw objects — human-readable prose that the LLM can reference.
    """
    lines = []
    for r in reports:
        all_sources = [r["primary_source"]] + list(r.get("corroboration_sources", []))
        seen: set[str] = set()
        unique_sources: list[str] = []
        for s in all_sources:
            if s and s not in seen:
                seen.add(s)
                unique_sources.append(s)
        source_list = " | ".join(unique_sources[:8])
        claim_text = r["claim"][:max_chars]

        line = (
            f"[{r['verdict']}] [ID:{r['finding_id'][:8]}] "
            f'"{claim_text}" '
            f"— sources: {source_list} "
            f"(confidence: {r['confidence_score']:.2f})"
        )
        for cp in r["conflicts"]:
            conflict_claim = cp["claim_b"][:max_chars]
            line += (
                f"\n  ↳ CONFLICT ({cp['conflict_type']}): "
                f'"{conflict_claim}" — {cp["source_b"]} | {cp["explanation"][:max_chars]}'
            )
        lines.append(line)
    return "\n".join(lines)


async def _invoke_synthesis_llm(
    query: str,
    reports: list[ConflictReport],
    verified: int,
    partially_verified: int,
    contested: int,
    unverified: int,
    sources: int,
    max_content_chars: int,
    job_id: str,
) -> dict:
    """
    Invoke the synthesis LLM with a 90s hard timeout.
    Returns parsed dict or raises on failure.
    """
    claims_block = _build_readable_findings(reports, max_chars=max_content_chars)

    response = await asyncio.wait_for(
        get_reasoning_llm().ainvoke([
            SystemMessage(content=SYNTHESIS_PROMPT),
            HumanMessage(content=(
                f"Original research query: {query}\n\n"
                f"Findings: {len(reports)} total "
                f"({verified} VERIFIED, {partially_verified} PARTIALLY_VERIFIED, "
                f"{contested} CONTESTED, {unverified} UNVERIFIED)\n"
                f"Unique sources: {sources}\n\n"
                f"FINDINGS WITH VERDICTS:\n{claims_block}"
            )),
        ]),
        timeout=90.0,
    )

    raw_content = response.content
    if isinstance(raw_content, list):
        content_len = sum(len(p.get("text", "")) if isinstance(p, dict) else len(str(p)) for p in raw_content)
    else:
        content_len = len(raw_content) if raw_content else 0
    logger.info("[%s] Synthesis: LLM response length=%d chars", job_id, content_len)

    parsed = _parse_report(raw_content)

    # Validate sections are present — if empty, this is incomplete
    if not parsed.get("sections"):
        logger.warning("[%s] Synthesis: LLM returned empty sections — will retry", job_id)
        raise SynthesisEmptySectionsError("LLM returned valid JSON but sections array is empty")

    return parsed


async def _invoke_fallback_prose_llm(
    query: str,
    reports: list[ConflictReport],
    job_id: str,
) -> dict:
    """
    Fallback: Make a shorter LLM call with only top 5 verified claims.
    Returns plain prose text — no JSON parsing required.
    If this also fails, returns None.
    """
    verified_claims = [r for r in reports if r["verdict"] == "VERIFIED"][:5]
    if not verified_claims:
        verified_claims = reports[:5]

    findings_text = "\n".join(
        f"- {r['claim'][:200]} (source: {r['primary_source']})"
        for r in verified_claims
    )

    fallback_prompt = (
        "You are a research report writer. Write a concise research summary "
        "in flowing journalistic prose. Do NOT use bullet points or numbered lists.\n\n"
        "Write exactly:\n"
        "1. A 3-sentence executive summary paragraph\n"
        "2. Two short analysis sections, each with a bold title and one paragraph\n"
        "3. Two open questions as prose sentences\n\n"
        "Do NOT return JSON. Write plain text with section headers marked by ##.\n\n"
        f"Research query: {query}\n\n"
        f"Key verified findings:\n{findings_text}"
    )

    try:
        response = await asyncio.wait_for(
            get_reasoning_llm().ainvoke([
                HumanMessage(content=fallback_prompt),
            ]),
            timeout=60.0,
        )

        text = response.content
        if isinstance(text, list):
            text = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in text
            )

        # Parse the plain text into sections
        sections_list = []
        parts = re.split(r'##\s+(.+)', text)
        # parts = ['intro text', 'Section 1 Title', 'Section 1 Content', ...]

        executive_summary = parts[0].strip() if parts else text[:500]

        i = 1
        while i + 1 < len(parts):
            heading = parts[i].strip()
            content = parts[i + 1].strip()
            if heading and content:
                sections_list.append({
                    "heading": heading,
                    "content": content,
                    "claims_referenced": [],
                    "sources": [],
                })
            i += 2

        # Build claims from reports
        claims = []
        for r in reports:
            badge = r["verdict"]
            if badge == "CONTESTED":
                badge = "PARTIALLY_VERIFIED"
            elif badge not in ("VERIFIED", "PARTIALLY_VERIFIED", "UNVERIFIED", "UNSUPPORTED"):
                badge = "UNVERIFIED"
            claims.append({
                "claim": r["claim"],
                "badge": badge,
                "sources": [r["primary_source"]] + list(r.get("corroboration_sources", []))[:7],
                "contradicting_sources": [cp["source_b"] for cp in r.get("conflicts", [])],
                "conflict_note": r.get("conflicts", [{}])[0].get("explanation") if r.get("conflicts") else None,
            })

        # If we got no sections from parsing, create minimal ones
        if not sections_list:
            sections_list = [{
                "heading": "Research Analysis",
                "content": executive_summary or text[:1000],
                "claims_referenced": [],
                "sources": [],
            }]

        logger.info("[%s] Synthesis: fallback prose LLM succeeded — %d sections", job_id, len(sections_list))

        return {
            "title": f"Research Report: {query}",
            "executive_summary": executive_summary,
            "summary": executive_summary,
            "claims": claims,
            "sections": sections_list,
            "open_questions": [],
        }

    except Exception as e:
        logger.error(
            "[%s] Synthesis: fallback prose LLM also failed: %s\n%s",
            job_id, e, traceback.format_exc(),
        )
        return None


def _build_degraded_report(
    reports: list[ConflictReport],
    query: str,
    job_id: str,
) -> dict:
    """
    Last resort: Build minimum valid report from already-computed findings.
    Only used when BOTH LLM calls fail completely.
    """
    logger.warning(
        "[%s] Synthesis: both LLM attempts failed — returning degraded report from findings", job_id
    )

    # Build executive summary prose from verified claims
    verified_claims = [r for r in reports if r["verdict"] == "VERIFIED"]
    if verified_claims:
        top_claims = verified_claims[:3]
        claim_sentences = ". ".join(r["claim"][:150] for r in top_claims)
        executive_summary = (
            f"Research on \"{query}\" identified {len(verified_claims)} verified findings "
            f"across {len(reports)} total claims analyzed from multiple independent sources. "
            f"The strongest evidence points to: {claim_sentences}. "
            f"Further analysis is needed to resolve {sum(1 for r in reports if r['verdict'] == 'CONTESTED')} contested claims."
        )
    else:
        executive_summary = (
            f"Research on \"{query}\" analyzed {len(reports)} claims from multiple sources. "
            f"None reached full verification threshold requiring three independent domains. "
            f"The findings require additional corroboration from diverse sources."
        )

    # Build claims list
    claims = []
    for r in reports:
        badge = r["verdict"]
        if badge == "CONTESTED":
            badge = "PARTIALLY_VERIFIED"
        elif badge not in ("VERIFIED", "PARTIALLY_VERIFIED", "UNVERIFIED", "UNSUPPORTED"):
            badge = "UNVERIFIED"
        claims.append({
            "claim": r["claim"],
            "badge": badge,
            "sources": [r["primary_source"]] + list(r.get("corroboration_sources", []))[:7],
            "contradicting_sources": [cp["source_b"] for cp in r.get("conflicts", [])],
            "conflict_note": r.get("conflicts", [{}])[0].get("explanation") if r.get("conflicts") else None,
        })

    # Group claims by theme for sections
    verified_section_claims = [r for r in reports if r["verdict"] == "VERIFIED"]
    other_section_claims = [r for r in reports if r["verdict"] != "VERIFIED"]

    sections = []
    if verified_section_claims:
        content_parts = []
        for r in verified_section_claims[:8]:
            content_parts.append(f"{r['claim'][:200]} [VERIFIED]")
        sections.append({
            "heading": "Key Verified Findings",
            "content": ". ".join(content_parts) + ".",
            "claims_referenced": [r["finding_id"][:8] for r in verified_section_claims[:8]],
            "sources": [],
        })
    if other_section_claims:
        content_parts = []
        for r in other_section_claims[:8]:
            content_parts.append(f"{r['claim'][:200]} [{r['verdict']}]")
        sections.append({
            "heading": "Claims Requiring Further Investigation",
            "content": ". ".join(content_parts) + ".",
            "claims_referenced": [r["finding_id"][:8] for r in other_section_claims[:8]],
            "sources": [],
        })

    # Ensure at least one section exists
    if not sections:
        content_parts = [f"{r['claim'][:200]} [{r['verdict']}]" for r in reports[:10]]
        sections.append({
            "heading": "Research Findings",
            "content": ". ".join(content_parts) + ".",
            "claims_referenced": [r["finding_id"][:8] for r in reports[:10]],
            "sources": [],
        })

    return {
        "title": f"Research Report: {query}",
        "executive_summary": executive_summary,
        "summary": executive_summary,
        "claims": claims,
        "sections": sections,
        "open_questions": [],
    }


async def synthesis_node(state: ResearchState) -> dict:
    """Write the final research report from conflict-annotated findings."""
    reports: list[ConflictReport] = state["conflict_reports"]
    query = state["query"]
    job_id = state["job_id"]

    if not reports:
        logger.error(
            "[%s] [AGENT_LOG] agent=synthesis input_size=0 status=failed "
            "error=SynthesisEmptySourcesError",
            job_id,
        )
        raise SynthesisEmptySourcesError(
            f"[{job_id}] synthesis_agent received 0 conflict reports. "
            "Dedup and conflict agents must produce output before synthesis can run."
        )

    _start = time.monotonic()

    verified = sum(1 for r in reports if r["verdict"] == "VERIFIED")
    partially_verified = sum(1 for r in reports if r["verdict"] == "PARTIALLY_VERIFIED")
    contested = sum(1 for r in reports if r["verdict"] == "CONTESTED")
    unverified = sum(1 for r in reports if r["verdict"] == "UNVERIFIED")
    all_source_urls: set[str] = set()
    for r in reports:
        all_source_urls.add(r["primary_source"])
        all_source_urls.update(r.get("corroboration_sources", []))
    sources = len(all_source_urls)

    logger.info("[%s] Synthesis: writing from %d findings (V=%d PV=%d C=%d U=%d) across %d sources",
                job_id, len(reports), verified, partially_verified, contested, unverified, sources)

    # ── Primary LLM attempt: full JSON report ─────────────────────────────
    parsed = None

    # First attempt: 400 char truncation
    try:
        parsed = await _invoke_synthesis_llm(
            query, reports, verified, partially_verified, contested,
            unverified, sources, max_content_chars=400, job_id=job_id,
        )
        logger.info("[%s] Synthesis: LLM call succeeded (400 char truncation)", job_id)
    except (json.JSONDecodeError, SynthesisEmptySectionsError) as e:
        logger.warning(
            "[%s] Synthesis: parse/validation failed on first attempt (400 chars): %s",
            job_id, e,
        )
        # Retry with shorter truncation
        try:
            parsed = await _invoke_synthesis_llm(
                query, reports, verified, partially_verified, contested,
                unverified, sources, max_content_chars=200, job_id=job_id,
            )
            logger.info("[%s] Synthesis: LLM retry succeeded (200 char truncation)", job_id)
        except Exception as retry_err:
            logger.warning(
                "[%s] Synthesis: LLM retry also failed (200 chars): %s",
                job_id, retry_err,
            )
            parsed = None
    except asyncio.TimeoutError:
        logger.error(
            "[%s] Synthesis: LLM call timed out (90s) — trying fallback", job_id,
        )
        parsed = None
    except Exception as e:
        logger.error(
            "[%s] Synthesis: LLM call failed with %s: %s\n%s",
            job_id, type(e).__name__, e, traceback.format_exc(),
        )
        # Retry with 200 char truncation
        try:
            parsed = await _invoke_synthesis_llm(
                query, reports, verified, partially_verified, contested,
                unverified, sources, max_content_chars=200, job_id=job_id,
            )
            logger.info("[%s] Synthesis: LLM retry succeeded (200 char truncation)", job_id)
        except Exception as retry_err:
            logger.error(
                "[%s] Synthesis: LLM retry also failed: %s\n%s",
                job_id, retry_err, traceback.format_exc(),
            )
            parsed = None

    # ── Fallback: shorter prose-only LLM call (no JSON needed) ────────────
    if parsed is None:
        logger.info("[%s] Synthesis: attempting fallback prose LLM call", job_id)
        parsed = await _invoke_fallback_prose_llm(query, reports, job_id)

    # ── Last resort: degraded report from raw findings ────────────────────
    if parsed is None:
        parsed = _build_degraded_report(reports, query, job_id)

    # ── Validate sections are not empty ───────────────────────────────────
    if not parsed.get("sections"):
        logger.warning("[%s] Synthesis: empty sections in parsed result — building from findings", job_id)
        verified_claims = [r for r in reports if r["verdict"] == "VERIFIED"]
        fallback_content = ". ".join(r["claim"][:200] + " [VERIFIED]" for r in verified_claims[:6])
        if not fallback_content:
            fallback_content = ". ".join(r["claim"][:200] + f" [{r['verdict']}]" for r in reports[:6])
        parsed["sections"] = [{
            "heading": "Research Findings",
            "content": fallback_content + ".",
            "claims_referenced": [],
            "sources": [],
        }]

    # ── Build ReportClaim list and enforce badge independence rule ─────────
    parsed_claims = parsed.get("claims", [])
    report_claims: list[ReportClaim] = []
    for c in parsed_claims:
        claim_obj = ReportClaim(
            claim=c.get("claim", ""),
            badge=c.get("badge", "UNVERIFIED"),
            sources=c.get("sources", []),
            contradicting_sources=c.get("contradicting_sources", []),
            conflict_note=c.get("conflict_note"),
        )
        claim_obj = _enforce_badge_independence(claim_obj)
        report_claims.append(claim_obj)

    # ── If LLM returned no claims, build from conflict reports ────────────
    if not report_claims:
        logger.warning("[%s] Synthesis: LLM returned no claims — building from conflict reports", job_id)
        for r in reports:
            badge = r["verdict"]
            if badge == "CONTESTED":
                badge = "PARTIALLY_VERIFIED"
            elif badge not in ("VERIFIED", "PARTIALLY_VERIFIED", "UNVERIFIED", "UNSUPPORTED"):
                badge = "UNVERIFIED"
            claim_obj = ReportClaim(
                claim=r["claim"],
                badge=badge,
                sources=[r["primary_source"]] + list(r.get("corroboration_sources", []))[:7],
                contradicting_sources=[cp["source_b"] for cp in r.get("conflicts", [])],
                conflict_note=r.get("conflicts", [{}])[0].get("explanation") if r.get("conflicts") else None,
            )
            claim_obj = _enforce_badge_independence(claim_obj)
            report_claims.append(claim_obj)

    # ── Build URL → publish_date lookup from deduped findings ─────────────
    url_to_date: dict[str, str] = {}
    for df in state.get("deduped_findings", []):
        pub = df.get("publication_date", "unknown")
        url_to_date[df["primary_source_url"]] = pub
        for corr_url in df.get("corroboration_sources", []):
            url_to_date.setdefault(corr_url, pub)

    # ── Build citation graph nodes + edges ────────────────────────────────
    all_urls: dict[str, CitationNode] = {}
    edges: list[CitationEdge] = []
    edge_seen: set[str] = set()

    def _make_node(url: str, badge: str) -> CitationNode:
        domain = urlparse(url).netloc.lstrip("www.") or url[:30]
        tier = assign_source_tier(domain)
        is_grounding = "gemini" in url.lower()
        node_color = _BADGE_COLORS.get(badge, "#ef4444")
        return CitationNode(
            id=f"src_{len(all_urls) + 1}",
            url=url,
            title=url.split("/")[-1][:60] or domain,
            domain=domain,
            provider="gemini_grounding" if is_grounding else (
                "tavily" if any(d in domain for d in ["reuters", "bbc", "apnews", "nyt"]) else "firecrawl"
            ),
            publish_date=url_to_date.get(url, "unknown"),
            badge_contribution=badge,
            credibility_tier=tier,
            node_color=node_color,
            node_border=_GEMINI_GROUNDING_BORDER if is_grounding else None,
            is_partial=is_grounding,
        )

    for claim_idx, claim in enumerate(report_claims):
        claim_text_preview = claim.get("claim", "")[:80]
        for url in claim.get("sources", []):
            if not url:
                continue
            if url not in all_urls:
                all_urls[url] = _make_node(url, claim.get("badge", "UNVERIFIED"))
            node_id = all_urls[url]["id"]
            edge_key = f"{claim_idx}-{node_id}-supports"
            if edge_key not in edge_seen:
                edge_seen.add(edge_key)
                edges.append(CitationEdge(
                    claim_index=claim_idx,
                    source_id=node_id,
                    relationship_type="supports",
                    claim_text_preview=claim_text_preview,
                ))
        for url in claim.get("contradicting_sources", []):
            if not url:
                continue
            if url not in all_urls:
                all_urls[url] = _make_node(url, "UNVERIFIED")
            node_id = all_urls[url]["id"]
            edge_key = f"{claim_idx}-{node_id}-contradicts"
            if edge_key not in edge_seen:
                edge_seen.add(edge_key)
                edges.append(CitationEdge(
                    claim_index=claim_idx,
                    source_id=node_id,
                    relationship_type="contradicts",
                    claim_text_preview=claim_text_preview,
                ))

    # ── Citation graph fallback ───────────────────────────────────────────
    generation_note: str | None = None
    if not all_urls and reports:
        logger.warning(
            "[%s] Synthesis: citation graph empty — using fallback direct mapping.",
            job_id,
        )
        for i, r in enumerate(reports):
            url = r["primary_source"]
            if url and url not in all_urls:
                all_urls[url] = _make_node(url, r.get("verdict", "UNVERIFIED"))
            if url:
                node_id = all_urls[url]["id"]
                edge_key = f"0-{node_id}-supports"
                if edge_key not in edge_seen:
                    edge_seen.add(edge_key)
                    edges.append(CitationEdge(
                        claim_index=0,
                        source_id=node_id,
                        relationship_type="supports",
                        claim_text_preview=r.get("claim", "")[:80],
                    ))
        generation_note = "Fallback graph — direct source mapping used"

    graph_generated = len(all_urls) > 0
    if not graph_generated:
        generation_note = "No source-claim relationships found"

    citation_graph = CitationGraphData(
        nodes=list(all_urls.values()),
        edges=edges,
        graph_generated=graph_generated,
        generation_note=generation_note,
    )
    logger.info("[%s] Synthesis: citation graph %d nodes, %d edges (generated=%s)",
                job_id, len(all_urls), len(edges), graph_generated)

    # ── Badge counts ──────────────────────────────────────────────────────
    if report_claims:
        verified_final       = sum(1 for c in report_claims if c["badge"] == "VERIFIED")
        partially_verified   = sum(1 for c in report_claims if c["badge"] == "PARTIALLY_VERIFIED")
        unverified_final     = sum(1 for c in report_claims if c["badge"] == "UNVERIFIED")
        unsupported_final    = sum(1 for c in report_claims if c["badge"] == "UNSUPPORTED")
        contested_final      = partially_verified
    else:
        verified_final = verified
        partially_verified = contested
        unverified_final = unverified
        unsupported_final = 0
        contested_final = contested

    # ── Source counts ─────────────────────────────────────────────────────
    sources_used = len(all_urls)
    lookback_hours = state.get("lookback_hours", 8760)
    sources_crawled_count = len({f["source_url"] for f in state.get("raw_findings", [])})
    sources_after_dedup_count = len({
        df["primary_source_url"] for df in state.get("deduped_findings", [])
    })
    conflicts_detected = sum(1 for r in reports if r.get("verdict") == "CONTESTED")
    low_coverage = sources_after_dedup_count < 6

    executive_summary = parsed.get("executive_summary", parsed.get("summary", ""))

    report = ResearchReport(
        title=parsed.get("title", f"Research Report: {query}"),
        summary=executive_summary,
        executive_summary=executive_summary,
        claims=report_claims,
        sections=[
            ReportSection(
                heading=s.get("heading", s.get("title", "")),
                content=s.get("content", ""),
                claims_referenced=s.get("claims_referenced", []),
                sources=s.get("sources", []),
            )
            for s in parsed.get("sections", [])
        ],
        open_questions=parsed.get("open_questions", []),
        total_sources=sources,
        sources_crawled=sources_crawled_count,
        sources_after_dedup=sources_after_dedup_count,
        sources_used=sources_used,
        verified_count=verified_final,
        contested_count=contested_final,
        unverified_count=unverified_final,
        claims_partially_verified=partially_verified,
        claims_unsupported=unsupported_final,
        conflicts_detected=conflicts_detected,
        low_coverage=low_coverage,
        query_type=state.get("query_type", "deep"),
        query_recency_window_hours=lookback_hours,
        generated_at=datetime.now(timezone.utc).isoformat(),
        citation_graph=citation_graph,
    )

    _duration_ms = int((time.monotonic() - _start) * 1000)
    logger.info("[%s] Synthesis: %d sections written, %d claims, duration=%dms",
                job_id, len(report["sections"]), len(report_claims), _duration_ms)
    return {
        "report": report,
        "status": "publishing",
        "agent_logs": [
            f"Synthesis: {len(report['sections'])}-section report, "
            f"{len(report_claims)} claims — "
            f"'{executive_summary[:100]}'"
        ],
    }
