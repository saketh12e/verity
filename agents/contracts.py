from typing import TypedDict, Literal, Optional


# ─── Query Classification ─────────────────────────────────────────────────────

class QueryClassification(TypedDict):
    query_type: Literal["deep", "breaking"]   # breaking = < 24h lookback
    lookback_hours: int                        # 8760 for deep, 6-24 for breaking


# ─── Citation Graph Types ─────────────────────────────────────────────────────

class CitationNode(TypedDict):
    id: str                    # "src_N"
    url: str
    title: str
    domain: str                # root domain only, e.g. "techcrunch.com"
    provider: str              # firecrawl | tavily | gemini_grounding
    publish_date: Optional[str]
    badge_contribution: str    # VERIFIED | PARTIALLY_VERIFIED | UNVERIFIED | UNSUPPORTED | none
    credibility_tier: str      # primary | secondary | low
    node_color: str            # hex — backend sets, frontend reads directly
    node_border: Optional[str] # hex or null — set for gemini_grounding sources
    is_partial: bool


class CitationEdge(TypedDict):
    claim_index: int
    source_id: str
    relationship_type: str     # supports | contradicts
    claim_text_preview: str    # first 80 chars of claim


class CitationGraphData(TypedDict):
    nodes: list[CitationNode]
    edges: list[CitationEdge]
    graph_generated: bool
    generation_note: Optional[str]


# ─── Report Claim ─────────────────────────────────────────────────────────────

class ReportClaim(TypedDict):
    claim: str
    badge: Literal["VERIFIED", "PARTIALLY_VERIFIED", "UNVERIFIED", "UNSUPPORTED"]
    sources: list[str]               # URLs supporting this claim
    contradicting_sources: list[str] # URLs opposing this claim
    conflict_note: Optional[str]


# ─── Decomposer Output ────────────────────────────────────────────────────────

class SubQuestion(TypedDict):
    id: int
    question: str
    angle: Literal[
        "scientific",
        "practitioner",
        "contrarian",
        "regulatory",
        "historical",
        "recent_news",
        "risk",
        "opinion"
    ]
    source_preference: Literal["arxiv", "reddit", "news", "web", "official", "any"]


class DecomposedQuery(TypedDict):
    original_query: str
    sub_questions: list[SubQuestion]


# ─── Crawler Output ───────────────────────────────────────────────────────────

class CrawlerFinding(TypedDict):
    id: str                      # uuid4
    claim: str                   # SINGLE atomic factual statement
    source_url: str
    source_domain: str
    source_tier: Literal["primary", "secondary", "opinion"]
    # primary   = academic paper, gov site, official docs, peer-reviewed
    # secondary = established news, industry analysis, verified expert
    # opinion   = blog, forum, social media, unverified commentary
    publication_date: str        # ISO date string or "unknown"
    sub_question_id: int
    raw_excerpt: str             # exact paragraph this claim came from


# ─── Dedup Agent Output ───────────────────────────────────────────────────────

class DedupedFinding(TypedDict):
    id: str
    claim: str
    primary_source_url: str
    corroboration_sources: list[str]   # all other URLs that said the same thing
    corroboration_count: int
    confidence_score: float            # 0.0 - 1.0
    source_tier: str
    publication_date: str
    sub_question_id: int


# ─── Conflict Detector Output ─────────────────────────────────────────────────

class ConflictPair(TypedDict):
    claim_a: str
    source_a: str
    claim_b: str
    source_b: str
    conflict_type: Literal[
        "direct_contradiction",   # sources flatly disagree
        "temporal_shift",         # old source says X, new source says opposite (truth changed)
        "scope_difference",       # both true but for different populations/contexts
    ]
    explanation: str              # one sentence explaining the conflict


class ConflictReport(TypedDict):
    finding_id: str
    claim: str
    verdict: Literal["VERIFIED", "PARTIALLY_VERIFIED", "CONTESTED", "UNVERIFIED"]
    confidence_score: float
    primary_source: str
    corroboration_sources: list[str]   # all supporting URLs beyond primary (from cluster)
    conflicts: list[ConflictPair]       # empty list if not CONTESTED


# ─── Synthesis Output ─────────────────────────────────────────────────────────

class SourceRef(TypedDict):
    url: str
    domain: str
    year: str


class ReportSection(TypedDict):
    heading: str
    content: str                  # prose with inline [VERIFIED], [CONTESTED], [UNVERIFIED] tags
    claims_referenced: list[str]  # finding IDs used in this section
    sources: list[SourceRef]      # structured source refs for frontend hyperlinking


class ResearchReport(TypedDict):
    title: str
    summary: str                          # 2-3 sentence executive summary
    executive_summary: Optional[str]      # replaces/supplements 'summary'
    claims: Optional[list[ReportClaim]]   # structured claim list with 4-tier badges
    sections: list[ReportSection]
    open_questions: list[str]
    # ── Source counts ──────────────────────────────────────────────────────────
    total_sources: int
    sources_crawled: Optional[int]        # unique source URLs across all raw findings
    sources_after_dedup: Optional[int]    # unique source URLs after dedup agent
    sources_used: Optional[int]           # unique sources that appear in final claims
    # ── Badge counts ───────────────────────────────────────────────────────────
    verified_count: int
    contested_count: int
    unverified_count: int
    claims_partially_verified: Optional[int]
    claims_unsupported: Optional[int]
    # ── Research context ───────────────────────────────────────────────────────
    conflicts_detected: Optional[int]
    low_coverage: Optional[bool]          # True if fewer than 6 sources after dedup
    query_type: Optional[str]             # "breaking" | "deep"
    query_recency_window_hours: Optional[int]
    generated_at: Optional[str]           # UTC ISO8601 — always system clock
    citation_graph: Optional[CitationGraphData]


# ─── Publisher Output ─────────────────────────────────────────────────────────

class PublisherResult(TypedDict):
    doc_url: str
    doc_id: str
    success: bool
