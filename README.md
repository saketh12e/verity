<div align="center">
  <img src="verity-logo.jpg" alt="Verity Logo" width="180" />
  <h1>Verity</h1>
  <p><strong>Research that knows what it doesn't know.</strong></p>

  <p>
    <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+" />
    <img src="https://img.shields.io/badge/LangGraph-0.2+-1C3C3C?style=flat-square" alt="LangGraph" />
    <img src="https://img.shields.io/badge/FastAPI-0.110+-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI" />
    <img src="https://img.shields.io/badge/React-18+-61DAFB?style=flat-square&logo=react&logoColor=black" alt="React" />
    <img src="https://img.shields.io/badge/Pinecone-Vector_DB-6534AC?style=flat-square" alt="Pinecone" />
    <img src="https://img.shields.io/badge/Firecrawl-Search-FF6B35?style=flat-square" alt="Firecrawl" />
  </p>
</div>

---

Verity is a **multi-agent deep research system** that doesn't just find information — it **audits it**.

Most AI research tools summarize. Verity runs parallel agents that actively search for disagreement, cross-check sources against each other, and label every claim in the final report with a confidence verdict:

| Badge | Meaning |
|---|---|
| ✓ `VERIFIED` | Multiple independent sources agree |
| ⚡ `CONTESTED` | Real disagreement found between credible sources |
| `?` `UNVERIFIED` | Single source, no corroboration found |

No existing deep research tool — Perplexity, Google Deep Research, Manus — does this. They summarize. **Verity audits.**

---

## How It Works

A single query flows through a pipeline of six specialized agents:

```
Query
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│  🧩  Decomposer Agent                                    │
│  Splits the query into 4 non-overlapping sub-questions   │
│  across: scientific · practitioner · contrarian · news   │
└───────────────────┬─────────────────────────────────────┘
                    │  LangGraph Send() — parallel fan-out
          ┌─────────┴────────────────────┐
          ▼         ▼         ▼          ▼
      🔍 Crawler  🔍 Crawler  🔍 Crawler  🔍 Crawler
      (Scientific)(Practitioner)(Contrarian)(Recent News)
          └─────────┬────────────────────┘
                    │  raw_findings accumulated via operator.add
                    ▼
┌─────────────────────────────────────────────────────────┐
│  🗃️  Dedup Agent                                         │
│  Embeds each claim → Pinecone cosine similarity          │
│  Merges duplicates, computes confidence_score            │
└───────────────────┬─────────────────────────────────────┘
                    ▼
┌─────────────────────────────────────────────────────────┐
│  ⚖️  Conflict Detector Agent                             │
│  Cross-checks claims within each sub-question group      │
│  Classifies: direct_contradiction · temporal_shift       │
│              scope_difference                            │
└───────────────────┬─────────────────────────────────────┘
                    ▼
┌─────────────────────────────────────────────────────────┐
│  🔥  Devil's Advocate Agent                              │
│  Targets the top-5 VERIFIED claims                       │
│  Actively searches for counterevidence to challenge them │
└───────────────────┬─────────────────────────────────────┘
                    ▼
┌─────────────────────────────────────────────────────────┐
│  📝  Synthesis Agent                                     │
│  Writes a structured research report in prose            │
│  Every claim tagged [VERIFIED] / [CONTESTED] / [UNVERIFIED]│
│  CONTESTED claims always show both sides — no false      │
│  consensus, no flattening of disagreement                │
└───────────────────┬─────────────────────────────────────┘
                    ▼
              Final Report
       (rendered in-app + PDF export)
```

---

## The Confidence System

Every claim in a Verity report carries one of three badges:

```
"Remote work increases productivity [VERIFIED ✓]"
     ↳ Reuters · Stanford Study · HBR  (3 independent corroborations)

"Remote work reduces output quality [CONTESTED ⚡]"
     ├── Source A (2019): "Output drops significantly" — McKinsey
     └── Source B (2024): "Quality improves with async culture" — GitLab
         Conflict type: temporal_shift

"Burnout rates up 12% post-pandemic [UNVERIFIED ?]"
     ↳ Forbes (single source, no corroboration found)
```

The confidence score behind each badge is computed from:
- **Corroboration count** — how many independent sources say the same thing
- **Source tier** — `primary` (arxiv, .gov, .edu) > `secondary` (Reuters, FT) > `opinion` (blogs, Reddit)
- **Conflict presence** — any detected disagreement immediately marks a claim `CONTESTED`

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent orchestration | [LangGraph](https://langchain-ai.github.io/langgraph/) with `Send()` for parallel fan-out |
| LLM | Google Gemini via `langchain-google-genai` |
| Web search + scrape | [Firecrawl](https://firecrawl.dev) `search()` API |
| Secondary search | [Tavily](https://tavily.com) |
| Vector DB (dedup) | [Pinecone](https://pinecone.io) — index: `verity-findings` |
| Citation graph | [NetworkX](https://networkx.org) |
| Backend | FastAPI + SSE streaming |
| Frontend | React 18 + Vite + Tailwind CSS |
| PDF export | ReportLab (pure Python, no system deps) |
| Observability | LangSmith (always on) |
| Package manager | [uv](https://github.com/astral-sh/uv) |

---

## Project Structure

```
verity/
├── agents/
│   ├── contracts.py          # All TypedDicts — every agent imports from here
│   ├── decomposer.py         # Query decomposition into sub-questions
│   ├── crawler.py            # Parallel web crawler (one per sub-question)
│   ├── dedup.py              # Pinecone-based claim deduplication
│   ├── conflict.py           # Cross-source conflict detection
│   ├── devil_advocate.py     # Adversarial challenger for VERIFIED claims
│   ├── synthesis.py          # Report writer with confidence labeling
│   ├── publisher.py          # Google Docs publisher (optional)
│   └── prompts/              # Prompt templates, one file per agent
│
├── graph/
│   ├── state.py              # ResearchState TypedDict with operator.add reducers
│   └── graph.py              # LangGraph wiring + Send() fan-out
│
├── api/
│   ├── main.py               # FastAPI app init + LangSmith setup
│   └── routes.py             # POST /research · GET /stream/{id} · GET /result/{id}
│
├── services/
│   ├── pinecone_client.py    # Pinecone init + upsert + query helpers
│   ├── firecrawl_client.py   # Firecrawl search wrapper
│   ├── citation_graph.py     # NetworkX citation graph builder
│   ├── pdf_exporter.py       # ReportLab PDF generation
│   ├── source_router.py      # Source tier classification
│   ├── factguard.py          # Claim validation utilities
│   ├── llm_factory.py        # LLM client factory
│   └── langsmith_setup.py    # LangSmith tracing config
│
├── frontend/
│   └── src/
│       ├── App.jsx
│       └── components/
│           ├── ChatPanel.jsx       # Left panel: input + history
│           ├── ResearchFeed.jsx    # Right panel: live agent feed
│           ├── AgentCard.jsx       # Per-agent status card
│           ├── ReportView.jsx      # Report with confidence badges
│           ├── CitationGraph.jsx   # Interactive citation graph
│           └── ConfidenceBadge.jsx # VERIFIED / CONTESTED / UNVERIFIED badge
│
├── tests/
│   ├── test_decomposer.py
│   ├── test_crawler.py
│   ├── test_dedup.py
│   └── test_conflict.py
│
├── pyproject.toml            # uv project config
├── langgraph.json            # LangGraph server config
└── .env.example              # All required environment variables
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Node.js 18+

### 1. Clone and install

```bash
git clone https://github.com/your-username/verity-agent.git
cd verity-agent

# Install Python dependencies
uv sync

# Install frontend dependencies
cd frontend && npm install && cd ..
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your API keys (see Environment Variables section below)
```

### 3. Start the backend

```bash
uv run uvicorn api.main:app --reload --port 8000
```

### 4. Start the frontend

```bash
cd frontend
npm run dev
# → http://localhost:5173
```

### 5. Run with LangGraph server (optional)

```bash
uv run langgraph dev
# → LangGraph Studio at http://localhost:2024
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the values below.

```bash
# LLM
GOOGLE_API_KEY=...              # Google Gemini API key

# Web Search + Scraping
FIRECRAWL_API_KEY=fc-...        # https://firecrawl.dev
TAVILY_API_KEY=tvly-...         # https://tavily.com (secondary search layer)

# Vector DB
PINECONE_API_KEY=...            # https://pinecone.io
PINECONE_INDEX_NAME=verity-findings
PINECONE_ENVIRONMENT=us-east-1-aws

# Observability (required — LangSmith is always on)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__...       # https://smith.langchain.com
LANGCHAIN_PROJECT=verity-research

# Google Docs publish (optional)
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

All keys are loaded via `python-dotenv`. **Never commit `.env` to version control.**

---

## API Reference

### `POST /research`

Start a new research job.

```json
// Request
{ "query": "Is remote work better or worse for productivity?" }

// Response
{ "job_id": "abc-123", "status": "started" }
```

### `GET /stream/{job_id}`

Server-Sent Events stream of live agent status updates.

```
data: {"agent": "decomposer", "status": "done", "message": "Generated 4 sub-questions"}
data: {"agent": "crawler_1", "status": "running", "message": "Searching peer-reviewed sources..."}
data: {"agent": "conflict_agent", "status": "done", "message": "Found 3 contested claims"}
```

### `GET /result/{job_id}`

Returns the complete research report once the job is finished.

```json
{
  "report": {
    "title": "Remote Work: Productivity & Mental Health",
    "summary": "...",
    "sections": [...],
    "verified_count": 12,
    "contested_count": 4,
    "unverified_count": 3
  },
  "doc_url": "https://docs.google.com/..."
}
```

### `GET /export/{job_id}/pdf`

Downloads the report as a formatted PDF.

---

## Frontend UI

The UI is a **two-panel split** built with React + Tailwind:

```
┌─────────────────────┬──────────────────────────────┐
│                     │                              │
│    CHAT PANEL       │    RESEARCH FEED / REPORT    │
│    (left 40%)       │    (right 60%)               │
│                     │                              │
│  Conversation       │  [While running]             │
│  history            │  Live agent status cards     │
│                     │  with pulse animations       │
│                     │                              │
│                     │  [When done]                 │
│                     │  Report with inline badges   │
│                     │  + interactive citation graph│
│  [Input box]        │  + PDF export button         │
└─────────────────────┴──────────────────────────────┘
```

Agent cards appear in real-time as each stage progresses:

| Agent | Icon | Status colors |
|---|---|---|
| Decomposer | 🧩 | waiting → running (blue pulse) → done |
| Crawlers ×4 | 🔍 | parallel execution shown simultaneously |
| Dedup | 🗃️ | shows dedup count |
| Conflict Detector | ⚖️ | highlights contested claim count |
| Devil's Advocate | 🔥 | shows challenges issued |
| Synthesis | 📝 | streaming report generation |

---

## Running Tests

```bash
uv run pytest tests/ -v
```

Individual test suites:

```bash
uv run pytest tests/test_decomposer.py -v
uv run pytest tests/test_crawler.py -v
uv run pytest tests/test_dedup.py -v
uv run pytest tests/test_conflict.py -v
```

---

## Demo Query

For the best demo experience, use:

> **"Is remote work better or worse for productivity and mental health?"**

This query naturally produces:
- Real scientific literature with mixed results
- Pre/post COVID temporal shifts (2020 research vs 2024 research diverges)
- Practitioner opinion splitting both ways
- Visible `CONTESTED` flags in the live feed as two crawlers return conflicting findings simultaneously

---

## Observability

All graph executions are traced to [LangSmith](https://smith.langchain.com) automatically.

Each trace shows the full agent execution tree: token usage, latency per node, and state at every step. Set `LANGCHAIN_PROJECT=verity-research` in `.env` to see all runs grouped under one project.

---
## License

MIT

---

<div align="center">
  <sub>Built with LangGraph · Firecrawl · Pinecone · FastAPI · React</sub>
</div>
