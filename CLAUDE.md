# VERITY — Deep Research Intelligence Agent
## Master Blueprint for Claude Code

> Read this entire file before writing a single line of code.
> Every architectural decision, agent contract, tech choice, and build order is defined here.
> Do not deviate unless explicitly told to.

---

## 1. WHAT THIS PROJECT IS

**Verity** is a multi-agent deep research system that does what no existing tool does:
it doesn't just find information — it **audits it**.

When a user asks a question, Verity:
1. Splits it into focused sub-questions (Decomposer Agent)
2. Runs 4 parallel crawlers hitting different source layers simultaneously (Crawler Agents via LangGraph `Send()`)
3. Deduplicates overlapping findings using Pinecone vector similarity (Dedup Agent)
4. Detects when two sources say opposite things and classifies the conflict type (Conflict Detector)
5. Writes a structured research report with confidence labels on every claim (Synthesis Agent)
6. Publishes the final report to Google Docs with every sentence hyperlinked to its source (Publisher Agent)

**The core differentiator:** Every claim in the output is tagged as one of:
- `VERIFIED` — multiple independent sources agree
- `CONTESTED` — real disagreement found between credible sources
- `UNVERIFIED` — single source, no corroboration

No existing deep research tool (Perplexity, Google Deep Research, Manus) does this.
They summarize. Verity audits.

---

## 2. TECH STACK — FIXED, DO NOT SUBSTITUTE

| Layer | Technology | Notes |
|---|---|---|
| Agent orchestration | LangGraph (Python) | Use `Send()` API for parallel fan-out |
| LLM | Claude claude-sonnet-4-20250514 | Default for all agents |
| Web search + scrape | Firecrawl `search()` API | NOT the deprecated `/deep-research` endpoint |
| Vector DB (dedup) | Pinecone | Index name: `verity-findings` |
| Embeddings | `text-embedding-3-small` (OpenAI) or `voyage-3` (Voyage AI) | For Pinecone upsert/query |
| Backend | FastAPI + Python | SSE for streaming agent status to frontend |
| Frontend | React + Vite + Tailwind CSS | Split-panel chat UI |
| Publish | Google Docs MCP (`google-docs-mcp`) | Via MCP tool calls |
| Observability | LangSmith | Always on — set env vars, never skip |
| Package manager | `uv` | Never use pip directly |
| Env management | `python-dotenv` | All secrets in `.env` |

---

## 3. PROJECT STRUCTURE — CREATE THIS EXACTLY

```
verity/
├── CLAUDE.md                    ← this file
├── pyproject.toml               ← uv project config
├── .env.example                 ← all required env vars listed
├── .gitignore
│
├── agents/
│   ├── __init__.py
│   ├── contracts.py             ← ALL TypedDicts live here. Write this FIRST.
│   ├── decomposer.py            ← Decomposer Agent
│   ├── crawler.py               ← Crawler Agent (reused for all 4 crawlers)
│   ├── dedup.py                 ← Dedup Agent (Pinecone)
│   ├── conflict.py              ← Conflict Detector Agent
│   ├── synthesis.py             ← Synthesis Agent
│   └── publisher.py             ← Publisher Agent (Google Docs MCP)
│
├── graph/
│   ├── __init__.py
│   ├── state.py                 ← ResearchState TypedDict with reducers
│   └── graph.py                 ← LangGraph graph wiring + compile
│
├── api/
│   ├── __init__.py
│   ├── main.py                  ← FastAPI app init
│   └── routes.py                ← POST /research, GET /status/{job_id}, GET /stream/{job_id}
│
├── services/
│   ├── __init__.py
│   ├── pinecone_client.py       ← Pinecone init + helper functions
│   ├── firecrawl_client.py      ← Firecrawl search wrapper
│   └── langsmith_setup.py       ← LangSmith env config
│
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   └── src/
│       ├── App.jsx
│       ├── main.jsx
│       ├── index.css
│       └── components/
│           ├── ChatPanel.jsx        ← Left panel: chat input + history
│           ├── ResearchFeed.jsx     ← Right panel: live agent activity feed
│           ├── AgentCard.jsx        ← Individual agent status card
│           ├── ReportView.jsx       ← Final report with badges
│           └── ConfidenceBadge.jsx  ← VERIFIED/CONTESTED/UNVERIFIED badge
│
└── tests/
    ├── test_decomposer.py
    ├── test_crawler.py
    ├── test_dedup.py
    └── test_conflict.py
```

---

## 4. BUILD ORDER — FOLLOW THIS EXACTLY

Do not skip steps. Do not build step N+1 before step N is working.

### Step 1: Project scaffolding
- Create the full directory structure above
- Write `pyproject.toml` with all dependencies
- Write `.env.example` with all required keys
- Write `.gitignore`

### Step 2: Write `agents/contracts.py` FIRST
- All TypedDicts go here before any agent is written
- Every agent imports from here, never defines its own types
- See Section 6 for exact contracts

### Step 3: Write `graph/state.py`
- `ResearchState` with Annotated reducers
- See Section 7 for exact state schema

### Step 4: Write services
- `services/pinecone_client.py` — init index, upsert, query helpers
- `services/firecrawl_client.py` — search wrapper that returns structured findings
- `services/langsmith_setup.py` — env var config

### Step 5: Write agents ONE BY ONE, test each in isolation
Order: decomposer → crawler → dedup → conflict → synthesis → publisher

### Step 6: Wire the graph in `graph/graph.py`
- Connect all agents
- Use `Send()` for crawler fan-out
- Test end-to-end with one hardcoded query

### Step 7: FastAPI backend
- POST `/research` — accepts query, kicks off graph, returns job_id
- GET `/stream/{job_id}` — SSE endpoint streaming agent status events
- Store job state in a simple in-memory dict for V1 (Redis for V2)

### Step 8: Frontend
- React split-panel UI
- Left: chat
- Right: live agent feed via SSE, then report
- See Section 10 for UI spec

---

## 5. ENVIRONMENT VARIABLES (`.env.example`)

```bash
# LLM
ANTHROPIC_API_KEY=sk-ant-...

# Web Search + Scraping
FIRECRAWL_API_KEY=fc-...

# Vector DB
PINECONE_API_KEY=...
PINECONE_INDEX_NAME=verity-findings
PINECONE_ENVIRONMENT=us-east-1-aws

# Embeddings (choose one)
OPENAI_API_KEY=...         # if using text-embedding-3-small
# VOYAGE_API_KEY=...       # if using voyage-3

# Observability
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__...
LANGCHAIN_PROJECT=verity-research

# Optional: Tavily for secondary search layer
TAVILY_API_KEY=tvly-...

# Google Docs (MCP handled separately via mcp config)
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

---

## 6. AGENT CONTRACTS (`agents/contracts.py`)

Write this file FIRST. Every other file imports from here.

```python
from typing import TypedDict, Literal, Optional


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
    verdict: Literal["VERIFIED", "CONTESTED", "UNVERIFIED"]
    confidence_score: float
    primary_source: str
    conflicts: list[ConflictPair]   # empty list if VERIFIED or UNVERIFIED


# ─── Synthesis Output ─────────────────────────────────────────────────────────

class ReportSection(TypedDict):
    heading: str
    content: str                  # prose with inline [VERIFIED], [CONTESTED], [UNVERIFIED] tags
    claims_referenced: list[str]  # finding IDs used in this section


class ResearchReport(TypedDict):
    title: str
    summary: str                  # 2-3 sentence executive summary
    sections: list[ReportSection]
    open_questions: list[str]     # questions the research raised but couldn't resolve
    total_sources: int
    verified_count: int
    contested_count: int
    unverified_count: int


# ─── Publisher Output ─────────────────────────────────────────────────────────

class PublisherResult(TypedDict):
    doc_url: str
    doc_id: str
    success: bool
```

---

## 7. GRAPH STATE (`graph/state.py`)

```python
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

    # Crawler outputs — Annotated with operator.add so parallel crawlers
    # accumulate into one list rather than overwriting each other
    raw_findings: Annotated[list[CrawlerFinding], operator.add]

    # Dedup output
    deduped_findings: list[DedupedFinding]

    # Conflict detection output
    conflict_reports: list[ConflictReport]

    # Synthesis output
    report: Optional[ResearchReport]

    # Publisher output
    published: Optional[PublisherResult]

    # Status tracking for SSE streaming
    status: str          # "decomposing" | "crawling" | "deduping" | "analyzing" | "synthesizing" | "publishing" | "done"
    agent_logs: Annotated[list[str], operator.add]   # accumulated log messages
```

---

## 8. LANGGRAPH WIRING (`graph/graph.py`)

### Critical pattern: `Send()` for parallel crawlers

```python
from langgraph.graph import StateGraph, END
from langgraph.constants import Send
from graph.state import ResearchState

def route_to_crawlers(state: ResearchState):
    """Fan out one Send() per sub-question to crawler nodes."""
    return [
        Send("crawler_agent", {
            "query": state["query"],
            "job_id": state["job_id"],
            "sub_question": sq,
            "raw_findings": [],
            "agent_logs": []
        })
        for sq in state["sub_questions"]
    ]

# Graph wiring
builder = StateGraph(ResearchState)

builder.add_node("decomposer", decomposer_node)
builder.add_node("crawler_agent", crawler_node)
builder.add_node("dedup_agent", dedup_node)
builder.add_node("conflict_agent", conflict_node)
builder.add_node("synthesis_agent", synthesis_node)
builder.add_node("publisher_agent", publisher_node)

builder.set_entry_point("decomposer")
builder.add_conditional_edges("decomposer", route_to_crawlers, ["crawler_agent"])
builder.add_edge("crawler_agent", "dedup_agent")
builder.add_edge("dedup_agent", "conflict_agent")
builder.add_edge("conflict_agent", "synthesis_agent")
builder.add_edge("synthesis_agent", "publisher_agent")
builder.add_edge("publisher_agent", END)

graph = builder.compile()
```

### IMPORTANT: `operator.add` on `raw_findings`

Because `raw_findings` uses `Annotated[list, operator.add]`, each crawler node returning `{"raw_findings": [finding1, finding2]}` will APPEND to the shared list, not overwrite it. This is how parallel crawlers merge their results without race conditions.

---

## 9. AGENT IMPLEMENTATION NOTES

### Decomposer Agent

```python
# agents/decomposer.py
# System prompt must instruct the LLM to:
# - Generate exactly 4 sub-questions (V1 scope)
# - Cover different angles: scientific, practitioner, contrarian, recent_news
# - Make questions non-overlapping — each must address a genuinely different dimension
# - Return structured JSON matching DecomposedQuery contract
# - DO NOT generate yes/no questions — all must be open research questions
```

Prompt discipline: "Generate 4 sub-questions that collectively cover the full epistemic space of the query. Each question must be from a different angle. Do not overlap. Return only valid JSON."

---

### Crawler Agent

```python
# agents/crawler.py
# Uses firecrawl.search() — NOT /deep-research (deprecated)
# One crawler per sub-question
# Search strategy:
#   1. Translate sub_question into an optimized search query (not copy-paste)
#   2. Call firecrawl.search(query, limit=5)
#   3. For each result, extract atomic claims (one claim per CrawlerFinding object)
#   4. Assign source_tier based on domain:
#      - arxiv.org, .gov, .edu, pubmed → "primary"
#      - reuters, bbc, ft, wsj, techcrunch → "secondary"
#      - medium, reddit, substack, personal blogs → "opinion"
```

CRITICAL: Force atomic claims. The LLM must extract ONE claim per object. Not "the article discusses many things." One claim. One source. One object.

```python
# Firecrawl search call
from firecrawl import FirecrawlApp

app = FirecrawlApp(api_key=os.getenv("FIRECRAWL_API_KEY"))

results = app.search(
    query=optimized_query,
    limit=5,
    scrape_options={"formats": ["markdown"], "only_main_content": True}
)
```

---

### Dedup Agent

```python
# agents/dedup.py
# Algorithm:
# 1. Embed each CrawlerFinding.claim using text-embedding-3-small
# 2. Upsert to Pinecone with finding.id as vector ID
# 3. For each finding, query Pinecone for top-5 similar (cosine)
# 4. If similarity > 0.88 → merge: keep claim, accumulate all source URLs
# 5. Compute confidence_score:
#    base = corroboration_count mapped to: 1→0.2, 2→0.4, 3→0.6, 4+→0.8
#    tier_boost: primary +0.2, secondary +0.1, opinion +0.0
#    confidence_score = min(base + tier_boost, 1.0)
```

```python
# Pinecone V3 SDK pattern
from pinecone import Pinecone

pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
index = pc.Index(os.getenv("PINECONE_INDEX_NAME"))

# Upsert
index.upsert(vectors=[{
    "id": finding["id"],
    "values": embedding,
    "metadata": {
        "claim": finding["claim"],
        "source_url": finding["source_url"],
        "source_tier": finding["source_tier"],
        "sub_question_id": finding["sub_question_id"]
    }
}])

# Query
results = index.query(
    vector=embedding,
    top_k=5,
    include_metadata=True
)
```

---

### Conflict Detector Agent

```python
# agents/conflict.py
# For each DedupedFinding, compare its claim against ALL other claims
# Comparison strategy:
#   1. Group findings by sub_question_id — findings answering the same sub-question
#      are most likely to conflict
#   2. For each pair within a group, ask the LLM:
#      "Do these two claims contradict each other? If yes, classify the conflict type."
#   3. Assign verdict:
#      - VERIFIED: corroboration_count >= 3 AND no conflict found
#      - CONTESTED: at least one ConflictPair exists
#      - UNVERIFIED: corroboration_count == 1 AND no conflict found
#
# conflict_type classification:
#   direct_contradiction: "X is true" vs "X is false/wrong"
#   temporal_shift: same claim but one source is 2+ years older — truth may have evolved
#   scope_difference: both true but for different populations, geographies, or contexts
```

---

### Synthesis Agent

```python
# agents/synthesis.py
# Input: list[ConflictReport] with full claim context
# Output: ResearchReport
#
# System prompt must instruct:
# - Write a real research report, not a list of bullet points
# - Group findings into thematic sections (not by source, by topic)
# - Use inline tags: append [VERIFIED], [CONTESTED], or [UNVERIFIED] after each claim
# - For CONTESTED claims: show BOTH sides. Do not average. Do not pick a winner.
# - Generate "Open Questions" section: what did the research raise that it couldn't resolve?
# - Do not flatten disagreement into false consensus
# - Do not make the report sound more certain than the data supports
```

---

### Publisher Agent

```python
# agents/publisher.py
# Uses Google Docs MCP to create and populate a document
# MCP server: google-docs-mcp (https://github.com/a-bonus/google-docs-mcp)
#
# Document structure:
# - Title: Research Report: {original_query}
# - Subtitle: Generated by Verity | {timestamp}
# - Summary block (highlighted)
# - Sections with H2 headings
# - Each claim with:
#   - Inline hyperlink to source URL
#   - Color coding: green=VERIFIED, orange=CONTESTED, gray=UNVERIFIED
# - Open Questions section
# - Source bibliography at end
#
# Note: Color coding in Google Docs requires named ranges or text formatting API
# Use text_foreground_color in the Docs API for V1
```

---

## 10. MCP CONNECTORS — FULL LIST

Configure these in your Claude Code MCP settings (`~/.claude/claude_code_config.json`):

### Primary (required for V1)

| MCP Server | Purpose | Package |
|---|---|---|
| `firecrawl` | Web search + content extraction | Already connected |
| `tavily` | Secondary search layer for Devil's Advocate | Already connected |
| `google-docs-mcp` | Publish research reports | `npx google-docs-mcp` |

### Secondary (add for uniqueness)

| MCP Server | Purpose | Notes |
|---|---|---|
| `@pinecone/mcp-server` | Manage Pinecone index + queries | Official Pinecone MCP |
| `notion-mcp` | Alternate publish target (Notion page) | Already connected |
| `gmail-mcp` | Email report delivery to user | Already connected |
| `google-calendar-mcp` | "Schedule a research deep-dive" feature | Future V2 |

### Research-layer MCPs (for crawler diversity)

| MCP Server | Purpose | Install |
|---|---|---|
| `@modelcontextprotocol/server-brave-search` | Independent web index (not Google-dependent) | `npx @modelcontextprotocol/server-brave-search` |
| `exa-mcp` | Neural semantic search — finds conceptually related content | `npx exa-mcp` |

### MCP config block for `mcp_config.json`

```json
{
  "mcpServers": {
    "firecrawl": {
      "command": "npx",
      "args": ["-y", "firecrawl-mcp"],
      "env": { "FIRECRAWL_API_KEY": "${FIRECRAWL_API_KEY}" }
    },
    "tavily": {
      "command": "npx",
      "args": ["-y", "@tavily/mcp-server"],
      "env": { "TAVILY_API_KEY": "${TAVILY_API_KEY}" }
    },
    "google-docs": {
      "command": "npx",
      "args": ["-y", "google-docs-mcp"],
      "env": {
        "GOOGLE_CLIENT_ID": "${GOOGLE_CLIENT_ID}",
        "GOOGLE_CLIENT_SECRET": "${GOOGLE_CLIENT_SECRET}"
      }
    },
    "brave-search": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-brave-search"],
      "env": { "BRAVE_API_KEY": "${BRAVE_API_KEY}" }
    }
  }
}
```

---

## 11. FASTAPI BACKEND

### Routes

```python
# POST /research
# Body: { "query": "string" }
# Returns: { "job_id": "uuid", "status": "started" }

# GET /stream/{job_id}
# SSE stream of agent status events
# Event format: data: {"agent": "crawler_1", "status": "running", "message": "Searching..."}

# GET /result/{job_id}
# Returns: full ResearchReport + doc_url when done
```

### SSE streaming pattern

```python
from fastapi.responses import StreamingResponse
import asyncio, json

job_logs: dict[str, list] = {}   # in-memory for V1

async def event_stream(job_id: str):
    last_index = 0
    while True:
        logs = job_logs.get(job_id, [])
        for event in logs[last_index:]:
            yield f"data: {json.dumps(event)}\n\n"
            last_index = len(logs)
        await asyncio.sleep(0.5)
        if job_logs.get(f"{job_id}_done"):
            break

@app.get("/stream/{job_id}")
async def stream(job_id: str):
    return StreamingResponse(event_stream(job_id), media_type="text/event-stream")
```

---

## 12. FRONTEND UI SPEC

### Layout: Two-panel split

```
┌─────────────────────┬──────────────────────────────┐
│                     │                              │
│    CHAT PANEL       │    RESEARCH FEED / REPORT    │
│    (left 40%)       │    (right 60%)               │
│                     │                              │
│  Past messages      │  [While running]             │
│  shown above        │  Agent cards with status     │
│                     │                              │
│                     │  [When done]                 │
│                     │  Full report with badges     │
│                     │                              │
│  [Input box]        │                              │
└─────────────────────┴──────────────────────────────┘
```

### Agent Card (while research is running)

```jsx
// AgentCard.jsx
// Props: { name, status, message, icon }
// status: "waiting" | "running" | "done" | "error"
// Color: waiting=gray, running=blue pulse, done=green, error=red

<div className="agent-card">
  <span className="agent-icon">{icon}</span>
  <span className="agent-name">{name}</span>
  <span className={`status-dot ${status}`} />
  <span className="agent-message">{message}</span>
</div>
```

Agent cards to show (in order of appearance):
- 🧩 Decomposer — "Splitting your question into research angles..."
- 🔍 Crawler 1 (Scientific) — "Searching peer-reviewed sources..."
- 🔍 Crawler 2 (Practitioner) — "Searching expert opinion..."
- 🔍 Crawler 3 (Contrarian) — "Searching for counterevidence..."
- 🔍 Crawler 4 (Recent News) — "Searching latest developments..."
- 🗃️ Dedup — "Removing duplicate findings..."
- ⚖️ Conflict Detector — "Cross-checking sources for disagreements..."
- 📝 Synthesis — "Writing your research report..."
- 📤 Publisher — "Publishing to Google Docs..."

### Confidence Badge Component

```jsx
// ConfidenceBadge.jsx
const colors = {
  VERIFIED: "bg-green-100 text-green-800 border-green-300",
  CONTESTED: "bg-yellow-100 text-yellow-800 border-yellow-300",
  UNVERIFIED: "bg-gray-100 text-gray-600 border-gray-300"
}

const icons = {
  VERIFIED: "✓",
  CONTESTED: "⚡",
  UNVERIFIED: "?"
}

export function ConfidenceBadge({ verdict }) {
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded border font-mono ${colors[verdict]}`}>
      {icons[verdict]} {verdict}
    </span>
  )
}
```

### Report View

Each claim in the report renders as:
```
"Remote work increases productivity [VERIFIED ✓] — Reuters, Stanford Study, HBR"
                                     ↑ green badge    ↑ 3 inline source links
```

For CONTESTED claims:
```
"Remote work reduces output quality [CONTESTED ⚡]"
  ├── Source A (2019): "Output quality drops significantly" — McKinsey
  └── Source B (2024): "Quality improves with async culture" — GitLab Report
      Conflict type: temporal_shift — this may reflect how practices evolved
```

---

## 13. LANGSMITH OBSERVABILITY

LangSmith tracing is ALWAYS ON. No exceptions.

```python
# services/langsmith_setup.py
import os

def setup_langsmith():
    """Call this at FastAPI startup."""
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGCHAIN_API_KEY", "")
    os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_PROJECT", "verity-research")

# In api/main.py
from services.langsmith_setup import setup_langsmith
setup_langsmith()
```

Every graph.invoke() call will automatically trace to LangSmith. You can view the full agent execution tree, token usage, latency per node, and state at each step at smith.langchain.com.

---

## 14. WHAT NOT TO DO — HARD RULES

1. **Never use the Firecrawl `/deep-research` endpoint.** It is deprecated as of June 2025. Use `firecrawl.search()`.

2. **Never use `asyncio.gather()` for crawler parallelism.** Use LangGraph `Send()`. The graph handles concurrency.

3. **Never define TypedDicts inside agent files.** All contracts live in `agents/contracts.py`. Import from there.

4. **Never hardcode API keys.** All secrets from `.env` via `python-dotenv`.

5. **Never let the Synthesis Agent flatten CONTESTED claims.** It must show both sides. The system prompt must explicitly forbid false consensus.

6. **Never skip LangSmith.** It's how you demo the system's behavior to recruiters.

7. **Never use `pip install` directly.** Use `uv add package-name`.

8. **Never store agent state inside the agent function.** All state flows through `ResearchState`.

9. **Never write a one-giant-file backend.** One file per agent. One file for routes. One file for graph.

10. **Never use `npm create react-app`.** Use `npm create vite@latest frontend -- --template react`.

---

## 15. PYPROJECT.TOML

```toml
[project]
name = "verity"
version = "0.1.0"
description = "Deep research intelligence agent with source conflict detection"
requires-python = ">=3.11"

dependencies = [
    "langgraph>=0.2.0",
    "langchain-anthropic>=0.1.0",
    "langchain-openai>=0.1.0",
    "langsmith>=0.1.0",
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.29.0",
    "firecrawl-py>=1.0.0",
    "pinecone-client>=3.0.0",
    "openai>=1.0.0",
    "python-dotenv>=1.0.0",
    "pydantic>=2.0.0",
    "httpx>=0.27.0",
    "uuid>=1.30",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

Install with: `uv sync`

---

## 16. V1 SCOPE vs V2 ROADMAP

### V1 — Build today (what Claude Code builds now)
- 4 crawlers (not 8)
- Pinecone dedup with cosine similarity
- Conflict detection with 3 verdict types
- Synthesis with confidence badges
- Google Docs publish
- React split-panel UI with live agent feed
- SSE status streaming
- LangSmith tracing

### V2 — After V1 is working
- Expand to 8 crawlers
- Devil's Advocate Agent (actively searches for counterevidence to high-confidence claims)
- Temporal Drift Detection (old vs new source disagreement as a special class)
- Citation Graph Agent (Neo4j — detects circular citation loops, downgrades those claims)
- Notion as alternate publish target
- Pinecone → Qdrant migration for self-hosted option
- User auth + research history

---

## 17. DEMO SCRIPT (for recruiter demo)

When demoing, use this exact query:
**"Is remote work better or worse for productivity and mental health?"**

This query guarantees:
- Real scientific literature (mixed results)
- Practitioner opinion (both sides)
- Post-COVID temporal shift (2020 vs 2024 research diverges)
- CONTESTED claims will appear naturally
- The conflict detection fires visibly in the live feed

The demo shows:
1. Question entered in chat
2. Agent cards appear one by one in the right panel
3. Two crawlers light up with conflicting findings simultaneously
4. Conflict Detector fires with a CONTESTED flag
5. Report renders with green/yellow/gray badges inline
6. Google Docs link appears at the end

This 90-second sequence is the entire pitch. Record it. Post it.

---

## 18. DOCUMENTATION REFERENCES

Claude Code should fetch and read these URLs before implementing each component:

| Component | Reference URL |
|---|---|
| LangGraph Send() API | https://langchain-ai.github.io/langgraph/how-tos/map-reduce/ |
| LangGraph StateGraph | https://langchain-ai.github.io/langgraph/reference/graphs/ |
| Firecrawl Search endpoint | https://docs.firecrawl.dev/features/search |
| Firecrawl Python SDK | https://docs.firecrawl.dev/sdks/python |
| Pinecone Python SDK v3 | https://docs.pinecone.io/reference/sdks/python/overview |
| Pinecone upsert | https://docs.pinecone.io/reference/api/2025-01/data-plane/upsert_records |
| LangSmith tracing setup | https://docs.langchain.com/langsmith/trace-with-langgraph |
| FastAPI SSE | https://fastapi.tiangolo.com/advanced/custom-response/ |
| Google Docs MCP | https://github.com/a-bonus/google-docs-mcp |

---

## 19. PROJECT NAME + IDENTITY

**Name:** Verity
**Tagline:** "Research that knows what it doesn't know."
**Domain (optional):** verity.research
**GitHub repo name:** `verity-agent`

**One-liner for resume:**
> Built Verity, a LangGraph multi-agent deep research system that detects source conflicts and scores claim confidence — producing auditable research reports with VERIFIED/CONTESTED/UNVERIFIED labels on every finding.

**One-liner for recruiter:**
> "It's not just an AI that researches for you — it has agents that argue with each other and tell you which parts of the answer to trust."

---

*End of CLAUDE.md — Claude Code should now have everything it needs to build Verity from scratch.*
