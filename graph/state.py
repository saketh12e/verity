from typing import TypedDict, Annotated, Optional
import operator
from agents.contracts import (
    SubQuestion, CrawlerFinding, DedupedFinding,
    ConflictReport, ResearchReport, PublisherResult
)


class ResearchState(TypedDict):
    # Input
    query: str
    job_id: str

    # Decomposer output
    sub_questions: list[SubQuestion]

    # Crawler outputs — operator.add lets parallel crawlers accumulate
    raw_findings: Annotated[list[CrawlerFinding], operator.add]

    # Dedup output
    deduped_findings: list[DedupedFinding]

    # Conflict detection output — also accumulates via devil_advocate Send() nodes
    conflict_reports: Annotated[list[ConflictReport], operator.add]

    # Synthesis output
    report: Optional[ResearchReport]

    # Publisher output
    published: Optional[PublisherResult]

    # Citation graph (serialized JSON for API + D3.js frontend)
    citation_graph_json: Optional[dict]

    # Per-crawler Send() slice — None in main graph
    sub_question: Optional[SubQuestion]

    # Per-devil-advocate Send() slice — None in main graph
    devil_advocate_target: Optional[ConflictReport]

    # Batched devil-advocate targets (top 5 VERIFIED claims) — set by route_to_devil_advocate
    devil_advocate_targets: Optional[list[ConflictReport]]

    # Query recency classification (set by decomposer)
    lookback_hours: int          # set by decomposer, default 8760
    query_type: str              # "deep" or "breaking", set by decomposer

    # Devil's Advocate per-claim metadata (accumulated across Send() fan-out)
    devil_advocate_metadata: Annotated[list[dict], operator.add]

    # Status + logs
    status: str
    agent_logs: Annotated[list[str], operator.add]
