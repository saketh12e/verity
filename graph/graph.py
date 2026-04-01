import logging
from langgraph.graph import StateGraph, END
from langgraph.types import Send           # V2: was langgraph.constants — now langgraph.types
from graph.state import ResearchState
from agents.decomposer import decomposer_node
from agents.crawler import crawler_node
from agents.dedup import dedup_node
from agents.conflict import conflict_node
from agents.devil_advocate import devil_advocate_node
from agents.synthesis import synthesis_node

logger = logging.getLogger(__name__)


def route_to_crawlers(state: ResearchState) -> list[Send]:
    """Fan out one Send() per sub-question — all 4 crawlers run in parallel."""
    return [
        Send(
            "crawler_agent",
            {
                "query": state["query"],
                "job_id": state["job_id"],
                "sub_question": sq,
                "sub_questions": state["sub_questions"],
                # Forward decomposer's temporal window so crawlers use the right
                # lookback_hours instead of always defaulting to 8760 (1 year).
                "lookback_hours": state.get("lookback_hours", 8760),
                "query_type": state.get("query_type", "deep"),
                "raw_findings": [],
                "deduped_findings": [],
                "conflict_reports": [],
                "report": None,
                "published": None,
                "citation_graph_json": None,
                "devil_advocate_target": None,
                "devil_advocate_metadata": [],
                "status": "crawling",
                "agent_logs": [],
            },
        )
        for sq in state["sub_questions"]
    ]


def route_to_devil_advocate(state: ResearchState) -> list[Send]:
    """
    Route to devil's advocate or directly to synthesis.

    Fix A1: filter to VERIFIED only, sort by corroboration_sources count desc,
    cap at 5 claims max. If 0 verified claims, skip DA entirely.
    """
    job_id = state.get("job_id", "?")
    conflict_reports = state.get("conflict_reports", [])

    # ── A1: Filter to VERIFIED badge only ─────────────────────────────────
    verified_reports = [r for r in conflict_reports if r["verdict"] == "VERIFIED"]
    total_verified = len(verified_reports)

    if total_verified == 0:
        skip_reason = "no_verified_claims_to_challenge"
        logger.info(
            "[%s] DA targeting 0 of %d verified claims (capped at 5) — "
            "skip_reason=%s — routing to synthesis",
            job_id, len(conflict_reports), skip_reason,
        )
        logger.info(
            "[%s] [AGENT_LOG] agent=devil_advocate_agent claims_targeted=0 "
            "adversarial_queries_run=0 counter_sources_found=0 challenges_produced=0 "
            "claims_with_strong_challenge=0 claims_with_no_challenge=0 "
            "duration_ms=0 status=skipped skip_reason=%s",
            job_id, skip_reason,
        )
        return [Send("synthesis_agent", state)]

    # ── A1: Sort by corroboration_sources count descending, cap at 5 ──────
    verified_reports.sort(
        key=lambda r: len(r.get("corroboration_sources", [])),
        reverse=True,
    )
    top_5 = verified_reports[:5]

    logger.info(
        "[%s] DA targeting %d of %d verified claims (capped at 5)",
        job_id, len(top_5), total_verified,
    )

    # ── Single Send() to DA node with all 5 claims bundled ────────────────
    # The DA node now handles sequential processing internally (Fix A2).
    return [
        Send(
            "devil_advocate",
            {
                **state,
                "devil_advocate_targets": top_5,
                # Clear per-node accumulators so DA returns only its own results
                "conflict_reports": [],
                "devil_advocate_metadata": [],
                "agent_logs": [],
            },
        )
    ]


# ── Graph wiring ─────────────────────────────────────────────────────────────

builder = StateGraph(ResearchState)

builder.add_node("decomposer", decomposer_node)
builder.add_node("crawler_agent", crawler_node)
builder.add_node("dedup_agent", dedup_node)
builder.add_node("conflict_agent", conflict_node)
builder.add_node("devil_advocate", devil_advocate_node)
builder.add_node("synthesis_agent", synthesis_node)

builder.set_entry_point("decomposer")
builder.add_conditional_edges("decomposer", route_to_crawlers, ["crawler_agent"])
builder.add_edge("crawler_agent", "dedup_agent")
builder.add_edge("dedup_agent", "conflict_agent")
builder.add_conditional_edges("conflict_agent", route_to_devil_advocate,
                               ["devil_advocate", "synthesis_agent"])
builder.add_edge("devil_advocate", "synthesis_agent")
builder.add_edge("synthesis_agent", END)

graph = builder.compile()
graph.name = "verity"
