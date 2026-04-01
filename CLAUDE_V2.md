# VERITY V2 — Complete Update Blueprint for Claude Code

> Read this entire file before touching any V1 code.
> This document supersedes V1 CLAUDE.md where they conflict.
> V2 is built ON TOP of V1 — do not rewrite working V1 code unless specified.
> The model here, particularly, we are using the flash model listed is 'gemini-3.1-flash-lite-preview' This should be used for the flash crawler agents. For reasoning agent it should be Gemini 3.1 Pro Preview. 
You can use the documentation getting from context7mcp. To get the latest information regarding the lang graph and lang chain integrations of the modules and classes.
---

## 1. WHAT CHANGES IN V2 — SUMMARY

| Area | V1 | V2 |
|---|---|---|
| LLM (reasoning agents) | Claude claude-sonnet-4-20250514 | `gemini-3.1-pro-preview` |
| LLM (crawler agents) | Claude claude-sonnet-4-20250514 | `gemini-3.1-flash-lite-preview` |
| LLM library | `langchain-anthropic` | `langchain-google-genai` |
| Citation graph | None | NetworkX (in-memory Python, no Docker) |
| Graph rendering | None | JSON serialized → D3.js force graph on frontend |
| Source routing | All Firecrawl | Intelligent Tavily + Firecrawl split by angle |
| Source coverage | ~13 sources | Target 30-40 unique sources |
| New agents | None | Devil's Advocate, Citation Verifier, Citation Graph |
| Streaming | Batch output | Token-by-token SSE from Gemini |
| Export | Frontend only | PDF download via weasyprint |
| Verification layer | None | Every agent output passes through FactGuard validator |
| Prompt quality | Basic | Engineered prompts with CoT, few-shot, output contracts |

---

## 2. LLM MIGRATION — ANTHROPIC → GOOGLE GEMINI

### Remove these packages
```bash
uv remove langchain-anthropic anthropic
```

### Add these packages
```bash
uv add langchain-google-genai google-generativeai
```

### New environment variables (add to .env)
```bash
GOOGLE_API_KEY=AIza...          # Google AI Studio key
GEMINI_REASONING_MODEL=gemini-3.1-pro-preview
GEMINI_FAST_MODEL=gemini-3.1-flash-lite-preview
```

### Remove from .env
```bash
# DELETE these lines:
ANTHROPIC_API_KEY=...
```

### New LLM Factory (`services/llm_factory.py`)

**Replace the entire file with this:**

```python
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from google.generativeai import GenerativeModel
import google.generativeai as genai

# Configure Google AI
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

def get_reasoning_llm():
    """
    Gemini 3.1 Pro Preview — for Decomposer, Conflict Detector,
    Devil's Advocate, Synthesis. High reasoning, slower, more expensive.
    Use where quality matters more than speed.
    """
    return ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_REASONING_MODEL", "gemini-3.1-pro-preview"),
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        temperature=0.1,
        max_output_tokens=8192,
        convert_system_message_to_human=True,   # Required for Gemini
    )

def get_fast_llm():
    """
    Gemini 3.1 Flash lite Preview — for all 4 Crawler agents.
    Runs in parallel via Send(), speed critical here.
    4x faster than Pro, costs 10x less per token.
    """
    return ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_FAST_MODEL", "gemini-3.1-flash-lite-preview"),
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        temperature=0.0,       # Zero temp for crawlers — deterministic claim extraction
        max_output_tokens=4096,
        convert_system_message_to_human=True,
    )

def get_streaming_client():
    """
    Native Gemini client for token-by-token streaming in Synthesis Agent.
    Use this instead of LangChain when SSE streaming is needed.
    """
    return GenerativeModel(
        model_name=os.getenv("GEMINI_REASONING_MODEL", "gemini-3.1-pro-preview")
    )
```

### All agent files — change this import pattern

**Every agent file: replace**
```python
# OLD — delete this
from langchain_anthropic import ChatAnthropic
llm = ChatAnthropic(model="claude-sonnet-4-20250514")
```

**With:**
```python
# NEW — use this
from services.llm_factory import get_reasoning_llm, get_fast_llm
llm = get_reasoning_llm()   # for Decomposer, Conflict, Devil's Advocate, Synthesis
llm = get_fast_llm()        # for all Crawler agents ONLY
```

---

## 3. NETWORKX CITATION GRAPH — NO DOCKER, PURE PYTHON

NetworkX is the right call. In-memory, zero setup, ships with the Python project.
No Neo4j. No FalkorDB. No Docker containers.

### Add package
```bash
uv add networkx
```

### New file: `services/citation_graph.py`

```python
"""
Citation Graph using NetworkX.
Stores source-to-source citation relationships found during crawling.
Detects circular citation loops.
Serializes to JSON for D3.js frontend rendering.
"""
import networkx as nx
from typing import Optional
import json


class CitationGraph:
    """
    In-memory directed graph of source citations.
    One instance per research job, lives in ResearchState.
    """

    def __init__(self):
        self.graph = nx.DiGraph()

    def add_source(self, url: str, domain: str, tier: str, title: str = ""):
        """Register a source as a node."""
        self.graph.add_node(url, domain=domain, tier=tier, title=title)

    def add_citation(self, source_url: str, cites_url: str):
        """Source A cites Source B → directed edge A → B."""
        if source_url != cites_url:
            self.graph.add_edge(source_url, cites_url)

    def detect_circular_citations(self) -> list[tuple[str, str]]:
        """
        Find pairs (A, B) where A cites B AND B cites A.
        These are citation loops — claims from looped sources
        get their confidence score reduced.
        Returns list of (url_a, url_b) tuples.
        """
        loops = []
        for node in self.graph.nodes():
            for neighbor in self.graph.successors(node):
                if self.graph.has_edge(neighbor, node):
                    pair = tuple(sorted([node, neighbor]))
                    if pair not in loops:
                        loops.append(pair)
        return loops

    def get_circular_domains(self) -> set[str]:
        """Returns set of domains involved in citation loops."""
        loops = self.detect_circular_citations()
        looped_urls = set()
        for url_a, url_b in loops:
            looped_urls.add(url_a)
            looped_urls.add(url_b)
        return looped_urls

    def is_in_loop(self, url: str) -> bool:
        """Check if a specific source is inside a citation loop."""
        return url in self.get_circular_domains()

    def to_json(self) -> dict:
        """
        Serialize to D3.js-compatible format.
        Frontend CitationGraph.jsx consumes this directly.
        """
        nodes = []
        for url, data in self.graph.nodes(data=True):
            nodes.append({
                "id": url,
                "domain": data.get("domain", url),
                "tier": data.get("tier", "opinion"),
                "title": data.get("title", ""),
                "in_loop": self.is_in_loop(url),
                # Node size in D3 scales with number of times cited
                "citations_received": self.graph.in_degree(url),
            })

        edges = []
        for source, target in self.graph.edges():
            edges.append({
                "source": source,
                "target": target,
                "is_loop": (
                    self.graph.has_edge(target, source)  # bidirectional = loop
                ),
            })

        loops = self.detect_circular_citations()

        return {
            "nodes": nodes,
            "edges": edges,
            "loops": [{"url_a": a, "url_b": b} for a, b in loops],
            "stats": {
                "total_sources": len(nodes),
                "total_citations": len(edges),
                "circular_loops": len(loops),
            }
        }

    def confidence_penalty(self, url: str) -> float:
        """
        Returns confidence penalty for a source.
        0.0 = no penalty (clean source)
        0.2 = in a citation loop (moderate penalty)
        """
        return 0.2 if self.is_in_loop(url) else 0.0
```

### Add to ResearchState (`graph/state.py`)

```python
from services.citation_graph import CitationGraph

class ResearchState(TypedDict):
    # ... existing fields ...
    citation_graph: Optional[CitationGraph]   # Add this field
    citation_graph_json: Optional[dict]        # Serialized for API response
```

### New agent: `agents/citation_graph_agent.py`

```python
"""
Citation Graph Agent.
Runs DURING the crawl phase — after each crawler returns its findings,
this agent extracts any outbound links from the crawled pages and
adds citation relationships to the NetworkX graph.

Runs as a lightweight post-processor on crawler output.
No LLM needed — pure link extraction logic.
"""
from services.citation_graph import CitationGraph
from agents.contracts import CrawlerFinding
from urllib.parse import urlparse


TRUSTED_DOMAINS = {
    "pubmed.ncbi.nlm.nih.gov", "arxiv.org", "nature.com",
    "thelancet.com", "nejm.org", "who.int", "cdc.gov",
    "reuters.com", "bbc.com", "ft.com", "apnews.com"
}


def run_citation_graph_agent(
    findings: list[CrawlerFinding],
    graph: CitationGraph,
    raw_links: dict[str, list[str]]   # {source_url: [linked_urls]}
) -> CitationGraph:
    """
    Takes crawler findings + raw outbound links from each page.
    Adds nodes and edges to the citation graph.
    Detects loops immediately.
    """
    for finding in findings:
        source_url = finding["source_url"]
        domain = finding["source_domain"]
        tier = finding["source_tier"]

        # Register the source as a node
        graph.add_source(url=source_url, domain=domain, tier=tier)

        # Add citation edges from this source's outbound links
        outbound = raw_links.get(source_url, [])
        for linked_url in outbound:
            linked_domain = urlparse(linked_url).netloc
            # Only track citations TO known/trusted sources
            # Avoids bloating the graph with ad links, nav links, etc.
            if any(td in linked_domain for td in TRUSTED_DOMAINS):
                graph.add_source(url=linked_url, domain=linked_domain, tier="primary")
                graph.add_citation(source_url=source_url, cites_url=linked_url)

    return graph
```

### New API route for citation graph

```python
# In api/routes.py — add this route
@app.get("/graph/{job_id}")
async def get_citation_graph(job_id: str):
    """Returns citation graph JSON for D3.js frontend rendering."""
    job = job_store.get(job_id)
    if not job or not job.get("citation_graph_json"):
        return {"nodes": [], "edges": [], "loops": [], "stats": {}}
    return job["citation_graph_json"]
```

### Frontend: `CitationGraph.jsx`

```jsx
// Uses D3.js force-directed graph to render source relationships
// Nodes: colored by tier (primary=blue, secondary=green, opinion=gray)
// Edges: arrows showing citation direction
// Loop nodes: pulsing red highlight
// Tooltip on hover: domain name + tier + citations received

import { useEffect, useRef } from "react"
import * as d3 from "d3"

export function CitationGraph({ jobId }) {
  const svgRef = useRef(null)

  useEffect(() => {
    fetch(`/graph/${jobId}`)
      .then(r => r.json())
      .then(data => renderGraph(data, svgRef.current))
  }, [jobId])

  return (
    <div className="citation-graph-container">
      <h3 className="text-sm font-mono text-gray-400 mb-2">Source Citation Network</h3>
      <svg ref={svgRef} width="100%" height="300" />
      <p className="text-xs text-gray-500 mt-1">
        Red nodes = circular citation loops (confidence penalized)
      </p>
    </div>
  )
}
```

---

## 4. INTELLIGENT SOURCE ROUTER (`services/source_router.py`)

The key insight: **don't restrict by domain. Route by epistemological angle.**
Let the search engine find the best source. Tier it dynamically after retrieval.

```python
"""
Source Router — decides which search client to use for each crawler angle.
Tavily: better for academic, news, recent content, temporal filtering.
Firecrawl: better for forums, structured site crawl, practitioner communities.
Both: for contrarian angle — maximum coverage needed.
"""
import os
from typing import Literal
from tavily import TavilyClient
from firecrawl import FirecrawlApp

tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
firecrawl = FirecrawlApp(api_key=os.getenv("FIRECRAWL_API_KEY"))


ROUTING_TABLE = {
    "scientific": {
        "client": "tavily",
        "query_suffix": "peer reviewed study systematic review meta-analysis evidence",
        "tavily_params": {
            "search_depth": "advanced",
            "max_results": 8,
            "include_domains": [
                "pubmed.ncbi.nlm.nih.gov", "arxiv.org", "nature.com",
                "thelancet.com", "nejm.org", "jamanetwork.com",
                "bmj.com", "cochrane.org", "cell.com", "science.org"
            ]
        }
    },
    "recent_news": {
        "client": "tavily",
        "query_suffix": "2024 2025 2026 latest findings update",
        "tavily_params": {
            "search_depth": "advanced",
            "max_results": 8,
            "topic": "news",
            "days": 365
        }
    },
    "practitioner": {
        "client": "firecrawl",
        "query_suffix": "expert practitioners experience advice opinion",
        "firecrawl_params": {
            "limit": 6,
            "scrape_options": {"formats": ["markdown"], "only_main_content": True}
        }
        # Firecrawl handles Reddit, HN, forums better than Tavily
    },
    "contrarian": {
        "client": "both",   # Run both, merge, deduplicate
        "query_suffix": "criticism problems risks downsides wrong misconceptions controversy",
        "tavily_params": {"search_depth": "advanced", "max_results": 5},
        "firecrawl_params": {"limit": 5}
    },
    "regulatory": {
        "client": "firecrawl",
        "query_suffix": "official guidelines policy regulation government advisory",
        "firecrawl_params": {
            "limit": 5,
            "scrape_options": {"formats": ["markdown"], "only_main_content": True}
        }
    },
    "historical": {
        "client": "tavily",
        "query_suffix": "history evolution consensus change decades research timeline",
        "tavily_params": {
            "search_depth": "advanced",
            "max_results": 6
        }
    },
    "risk": {
        "client": "tavily",
        "query_suffix": "risks side effects dangers warnings adverse",
        "tavily_params": {"search_depth": "advanced", "max_results": 6}
    },
    "opinion": {
        "client": "firecrawl",
        "query_suffix": "community opinion discussion forum debate",
        "firecrawl_params": {"limit": 5}
    }
}


def assign_source_tier(domain: str) -> str:
    """
    Dynamic tier assignment AFTER retrieval — not before.
    Source quality is discovered, not assumed.
    """
    PRIMARY = [
        "pubmed", "arxiv", "nature.com", "nejm.org", "thelancet.com",
        "who.int", "cdc.gov", ".gov", ".edu", "nih.gov", "cochrane.org",
        "science.org", "cell.com", "bmj.com", "jamanetwork.com"
    ]
    SECONDARY = [
        "reuters.com", "bbc.com", "ft.com", "apnews.com", "wsj.com",
        "techcrunch.com", "wired.com", "arstechnica.com", "theatlantic.com",
        "economist.com", "nytimes.com", "theguardian.com"
    ]
    if any(s in domain for s in PRIMARY):
        return "primary"
    elif any(s in domain for s in SECONDARY):
        return "secondary"
    return "opinion"


async def route_and_search(angle: str, query: str) -> list[dict]:
    """
    Routes query to correct client based on angle.
    Returns raw search results (list of {url, title, content}).
    """
    strategy = ROUTING_TABLE.get(angle, ROUTING_TABLE["recent_news"])
    client = strategy["client"]
    full_query = f"{query} {strategy['query_suffix']}"

    results = []

    if client in ("tavily", "both"):
        params = strategy.get("tavily_params", {})
        tavily_results = tavily.search(full_query, **params)
        for r in tavily_results.get("results", []):
            results.append({
                "url": r["url"],
                "title": r.get("title", ""),
                "content": r.get("content", ""),
                "source": "tavily"
            })

    if client in ("firecrawl", "both"):
        params = strategy.get("firecrawl_params", {})
        fc_results = firecrawl.search(full_query, **params)
        for r in fc_results.get("data", []):
            results.append({
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "content": r.get("markdown", r.get("description", "")),
                "source": "firecrawl"
            })

    # Deduplicate by URL
    seen = set()
    unique = []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)

    return unique
```

---

## 5. FACTGUARD — VERIFICATION LAYER FOR ALL AGENTS

Every agent output passes through FactGuard before entering the LangGraph state.
This is the robustness layer. It catches hallucinated claims, malformed outputs,
and low-confidence extractions before they poison downstream agents.

### New file: `services/factguard.py`

```python
"""
FactGuard — Output verification layer.
Runs after every agent, before state update.
Catches: hallucinated sources, empty claims, malformed JSON,
         claims without evidence, suspiciously vague statements.
"""
from agents.contracts import CrawlerFinding, DedupedFinding, ConflictReport
import re
from urllib.parse import urlparse


class FactGuardViolation(Exception):
    """Raised when an agent output fails verification."""
    pass


def verify_crawler_finding(finding: CrawlerFinding) -> CrawlerFinding:
    """
    Rules:
    1. claim must be a single sentence (no multi-claim dumps)
    2. source_url must be a real URL (http/https, valid domain)
    3. claim must not be vague filler ("the article discusses...", "sources say...")
    4. raw_excerpt must exist and be non-empty
    5. claim length: 20-300 characters (too short = vague, too long = multi-claim)
    """
    claim = finding.get("claim", "").strip()
    url = finding.get("source_url", "")
    excerpt = finding.get("raw_excerpt", "").strip()

    # Rule 1: single sentence check
    sentences = [s.strip() for s in re.split(r'[.!?]', claim) if s.strip()]
    if len(sentences) > 2:
        raise FactGuardViolation(
            f"Multi-claim detected. Split into atomic claims. Got: '{claim[:80]}...'"
        )

    # Rule 2: URL validity
    try:
        parsed = urlparse(url)
        assert parsed.scheme in ("http", "https")
        assert "." in parsed.netloc
    except (AssertionError, Exception):
        raise FactGuardViolation(f"Invalid source URL: '{url}'")

    # Rule 3: vague filler check
    VAGUE_PATTERNS = [
        r"^the article (discusses|mentions|talks about)",
        r"^sources (say|suggest|indicate)",
        r"^it (is|was) (noted|mentioned|stated) that",
        r"^according to (various|multiple|some) sources",
        r"^researchers (have|found|suggest)",  # too vague without specifics
    ]
    for pattern in VAGUE_PATTERNS:
        if re.match(pattern, claim.lower()):
            raise FactGuardViolation(
                f"Vague claim detected: '{claim}'. Must be specific and attributable."
            )

    # Rule 4: excerpt must exist
    if len(excerpt) < 20:
        raise FactGuardViolation("raw_excerpt too short or missing. Must quote source text.")

    # Rule 5: claim length
    if len(claim) < 20:
        raise FactGuardViolation(f"Claim too vague (< 20 chars): '{claim}'")
    if len(claim) > 350:
        raise FactGuardViolation(f"Claim too long (> 350 chars) — likely multi-claim dump.")

    return finding


def verify_conflict_report(report: ConflictReport) -> ConflictReport:
    """
    Rules:
    1. verdict must be one of VERIFIED / CONTESTED / UNVERIFIED
    2. CONTESTED reports must have at least one ConflictPair
    3. Each ConflictPair must have non-empty claim_a, source_a, claim_b, source_b
    4. conflict_type must be valid enum value
    """
    VALID_VERDICTS = {"VERIFIED", "CONTESTED", "UNVERIFIED"}
    VALID_CONFLICT_TYPES = {"direct_contradiction", "temporal_shift", "scope_difference"}

    verdict = report.get("verdict", "")
    if verdict not in VALID_VERDICTS:
        raise FactGuardViolation(f"Invalid verdict: '{verdict}'. Must be one of {VALID_VERDICTS}")

    conflicts = report.get("conflicts", [])
    if verdict == "CONTESTED" and not conflicts:
        raise FactGuardViolation("CONTESTED verdict requires at least one ConflictPair.")

    for pair in conflicts:
        for field in ["claim_a", "source_a", "claim_b", "source_b"]:
            if not pair.get(field, "").strip():
                raise FactGuardViolation(f"ConflictPair missing field: '{field}'")
        if pair.get("conflict_type") not in VALID_CONFLICT_TYPES:
            raise FactGuardViolation(
                f"Invalid conflict_type: '{pair.get('conflict_type')}'"
            )

    return report


def safe_verify(finding, verifier_fn):
    """
    Wraps a verifier. On violation, returns the finding with a
    'factguard_flagged' field set to True and the reason.
    Never crashes the pipeline — flags and continues.
    """
    try:
        return verifier_fn(finding)
    except FactGuardViolation as e:
        finding["factguard_flagged"] = True
        finding["factguard_reason"] = str(e)
        return finding
```

---

## 6. ENGINEERED AGENT PROMPTS — ALL AGENTS

Prompt engineering principles applied throughout:
- **Chain-of-thought (CoT):** Think before outputting
- **Few-shot examples:** Show good vs bad output
- **Output contract:** Exact JSON schema in the prompt
- **Negative examples:** Explicitly show what NOT to do
- **Epistemic hedging rules:** What language to use at each confidence level

---

### 6.1 Decomposer Agent Prompt

```python
DECOMPOSER_SYSTEM = """You are an expert research strategist. Your job is to decompose a user's research question into exactly 4 focused sub-questions that together cover the full epistemic space of the topic.

## Your Reasoning Process (follow this exactly)

Step 1 — Identify the epistemic dimensions of the question:
  - What does science/research say? (empirical dimension)
  - What do practitioners/experts say from experience? (applied dimension)
  - What is the strongest counterargument or criticism? (contrarian dimension)
  - What has changed recently — is old consensus being revised? (temporal dimension)

Step 2 — Generate one sub-question per dimension. Each must:
  - Be a specific, answerable research question (not yes/no)
  - Cover a genuinely different angle than the others
  - NOT overlap with other sub-questions

Step 3 — Assign each sub-question an angle and source preference.

## Output Format (strict JSON only, no preamble)

{
  "original_query": "<user's exact query>",
  "reasoning": "<2-3 sentences explaining how you identified the 4 dimensions>",
  "sub_questions": [
    {
      "id": 1,
      "question": "<specific research question>",
      "angle": "scientific",
      "source_preference": "arxiv"
    },
    ...
  ]
}

## Good vs Bad Examples

BAD sub-question: "Is X good or bad?" (yes/no, vague)
GOOD sub-question: "What do randomized controlled trials published after 2020 show about X's effect on Y?"

BAD sub-question: "What do people think about X?" (too vague)
GOOD sub-question: "What criticisms have domain experts raised about the methodology of pro-X studies?"

BAD: All 4 questions asking the same thing from slightly different angles.
GOOD: 4 questions where each one, if answered alone, gives only 25% of the full picture.

## Angles available
scientific, practitioner, contrarian, recent_news, regulatory, historical, risk, opinion
Pick the 4 most relevant for this specific query."""
```

---

### 6.2 Crawler Agent Prompt

```python
CRAWLER_SYSTEM = """You are a precision claim extractor. You receive raw web content from a source and extract factual claims as atomic, structured objects.

## Your Core Rule — ONE CLAIM PER OBJECT

A claim is ONE specific, attributable factual statement.
Never bundle two facts into one claim object.

## Reasoning Process

Step 1 — Read the source content carefully.
Step 2 — Identify factual statements that directly address the research question.
Step 3 — For each statement, ask: "Is this a specific, verifiable fact?" If yes, extract it.
Step 4 — Find the exact sentence in the source that proves this claim (raw_excerpt).
Step 5 — Classify the source tier based on domain.

## Output Format (strict JSON array, no preamble)

[
  {
    "claim": "<single specific factual statement, 20-300 characters>",
    "source_url": "<exact URL>",
    "source_domain": "<domain only, e.g. pubmed.ncbi.nlm.nih.gov>",
    "source_tier": "primary|secondary|opinion",
    "publication_date": "<ISO date or 'unknown'>",
    "sub_question_id": <integer>,
    "raw_excerpt": "<exact quote from source that proves this claim, min 30 chars>"
  }
]

## Good vs Bad Claim Examples

BAD: "The article discusses the benefits and risks of intermittent fasting."
WHY BAD: Vague, describes the article not a fact, not attributable.

GOOD: "A 2023 meta-analysis of 27 RCTs found intermittent fasting reduced HbA1c by 0.8% in type 2 diabetics."
WHY GOOD: Specific, quantified, attributable, single fact.

BAD: "Researchers found that fasting improves health in multiple ways including metabolism, inflammation, and brain function."
WHY BAD: Three separate claims bundled. Split into 3 objects.

GOOD (3 separate objects):
  - "Researchers found 16:8 fasting reduced inflammatory markers (CRP) by 35% in a 12-week trial."
  - "A 2022 study linked intermittent fasting to 18% improvement in insulin sensitivity."
  - "BDNF levels increased 20% in participants following alternate-day fasting for 8 weeks."

## Source Tier Rules
primary: pubmed, arxiv, .gov, .edu, who.int, cochrane, nature, lancet, nejm
secondary: reuters, bbc, ft, wsj, apnews, techcrunch, wired, guardian
opinion: everything else (blogs, reddit, medium, substack, personal sites)

## What NOT to extract
- Paywalled content you cannot read (skip, do not hallucinate)
- Claims that don't address the specific sub-question assigned
- Opinions presented as facts
- Claims you are not 100% certain came from this source"""
```

---

### 6.3 Devil's Advocate Agent Prompt

```python
DEVIL_ADVOCATE_SYSTEM = """You are the Devil's Advocate Agent. Your ONLY job is to challenge high-confidence findings.

You will receive a list of VERIFIED claims — things the system believes to be well-supported.
Your mission: find credible evidence that contradicts, weakens, or adds important nuance to each one.

## You Are NOT Trying to Be Balanced

You are adversarial by design. You are not a neutral summarizer.
If a claim has been verified by 4 sources, search specifically for the 5th source that disagrees.
If you cannot find contradiction, that is valuable information — the claim survives scrutiny.

## Reasoning Process

For each VERIFIED claim you receive:
Step 1 — Identify the core assertion being made.
Step 2 — Formulate a search query that would find counterevidence.
        Rule: Never use the same words as the original claim in your search.
        Use antonyms, "risks of", "problems with", "criticism of", "debunking".
Step 3 — Search using the available tools:
  - Use tavily_search for academic papers, news, and recent findings
  - Use firecrawl_search for forums, Reddit, practitioner communities,
    and sites Tavily doesn't index well
  - For primary-tier source claims: prioritize tavily_search first
  - For opinion/secondary claims: prioritize firecrawl_search first
  You may call both tools if the first returns weak counterevidence.
Step 4 — Evaluate what you found:
  a) Strong counterevidence found → return as CHALLENGE_FOUND
  b) Weak or tangential counterevidence → return as NUANCE_FOUND
  c) Nothing credible found → return as SURVIVES_SCRUTINY

## Output Format (strict JSON, no preamble)

{
  "challenges": [
    {
      "original_claim": "<exact claim being challenged>",
      "original_source": "<URL of the verified claim>",
      "result": "CHALLENGE_FOUND|NUANCE_FOUND|SURVIVES_SCRUTINY",
      "counterevidence": "<specific counterevidence found, or empty string>",
      "counter_source": "<URL of counterevidence, or empty string>",
      "search_queries_used": ["<query 1>", "<query 2>"],
      "reasoning": "<why this is CHALLENGE vs NUANCE vs SURVIVES>"
    }
  ]
}

## Verdict Impact
CHALLENGE_FOUND → downgrade the original claim from VERIFIED to CONTESTED
NUANCE_FOUND → keep VERIFIED but add nuance note to the claim text
SURVIVES_SCRUTINY → confidence score increases to 0.95

## What counts as strong counterevidence
- A peer-reviewed study directly contradicting the claim
- An official body (WHO, CDC, FDA, government) issuing a warning or reversal
- A more recent meta-analysis that overturns an older finding

## What does NOT count
- A blog post disagreeing
- An anecdote or single case study
- An opinion piece from a non-expert
- A finding from a different population/context (that's NUANCE not CHALLENGE)"""
```

---

### 6.4 Synthesis Agent Prompt (Streaming-Compatible)

```python
SYNTHESIS_SYSTEM = """You are Verity's Synthesis Agent. You write research reports that are honest about uncertainty.

## Core Principle — Epistemic Honesty

The single most important rule: Do NOT make the report sound more certain than the data supports.
A report that accurately represents uncertainty is more valuable than one that sounds confident but misleads.

## Language Rules by Confidence Level

VERIFIED claims (confidence >= 0.7, no conflicts):
  Use: "Evidence shows...", "Research consistently finds...", "Studies confirm..."
  Do NOT use: "It is proven that...", "There is no doubt that..."

CONTESTED claims (conflicts exist):
  Use: "Evidence is divided on...", "Sources disagree about...", "One view holds X, while others find Y..."
  Do NOT use: "Some say X." (too dismissive of the disagreement)
  ALWAYS show both sides explicitly.

UNVERIFIED claims (single source, no corroboration):
  Use: "One study suggests...", "A single source reports...", "This remains to be confirmed by..."
  Do NOT use: "Research shows..." or "Studies find..." (plural implies multiple sources)

## Structure (always follow this order)

1. Executive Summary (3-5 sentences covering the main finding and key uncertainty)
2. Main Findings (grouped by theme, not by source)
3. Points of Disagreement (CONTESTED claims get their own section)
4. What We Don't Know (Open Questions — things research raised but couldn't resolve)
5. Source Quality Note (brief summary of source tier distribution)

## Inline Tagging

After every factual claim in the report, append the verdict tag:
  [VERIFIED ✓] — green in UI
  [CONTESTED ⚡] — yellow in UI
  [UNVERIFIED ?] — gray in UI

For CONTESTED claims, immediately follow with:
  > Source A (domain, year): <their position>
  > Source B (domain, year): <their position>
  > Conflict type: direct_contradiction | temporal_shift | scope_difference

## What You Must Never Do
- Never write "Research shows" when only one source was found
- Never average CONTESTED claims into a single "balanced" statement
- Never omit the [UNVERIFIED] tag to make the report sound more credible
- Never pick a winner in a genuine scientific dispute
- Never write more than 5 sentences without a source attribution"""
```

---

## 7. STREAMING SYNTHESIS — TOKEN BY TOKEN SSE

Replace the batch synthesis call with streaming.

### In `agents/synthesis.py`

```python
import google.generativeai as genai
import asyncio
import json
from services.llm_factory import get_streaming_client

async def run_synthesis_streaming(
    conflict_reports: list,
    job_id: str,
    push_event_fn  # async callable: push_event_fn(job_id, event_dict)
) -> str:
    """
    Streams synthesis output token by token.
    Each token is pushed to SSE immediately.
    Returns complete synthesis text when done.
    """
    client = get_streaming_client()

    synthesis_prompt = build_synthesis_prompt(conflict_reports)  # builds the user message

    full_text = ""

    # Gemini streaming
    response = await asyncio.to_thread(
        client.generate_content,
        synthesis_prompt,
        stream=True,
        generation_config=genai.GenerationConfig(
            temperature=0.1,
            max_output_tokens=8192,
        )
    )

    for chunk in response:
        if chunk.text:
            full_text += chunk.text
            await push_event_fn(job_id, {
                "type": "synthesis_token",
                "token": chunk.text,
                "agent": "synthesis"
            })

    # Signal synthesis complete
    await push_event_fn(job_id, {
        "type": "synthesis_complete",
        "agent": "synthesis",
        "total_length": len(full_text)
    })

    return full_text
```

### Frontend streaming consumer

```jsx
// In ResearchFeed.jsx — update SSE handler

useEffect(() => {
  const es = new EventSource(`/stream/${jobId}`)

  es.onmessage = (event) => {
    const data = JSON.parse(event.data)

    if (data.type === "synthesis_token") {
      // Append token to report text in real time
      setSynthesisText(prev => prev + data.token)
    }

    if (data.type === "agent_status") {
      updateAgentCard(data.agent, data.status, data.message)
    }

    if (data.type === "synthesis_complete") {
      setResearchDone(true)
    }
  }

  return () => es.close()
}, [jobId])
```

---

## 8. PDF EXPORT

```bash
uv add weasyprint
```

### New service: `services/pdf_exporter.py`

```python
from weasyprint import HTML
from datetime import datetime

def generate_pdf(report_html: str, query: str, stats: dict) -> bytes:
    """
    Renders the research report HTML to PDF.
    Returns PDF bytes for streaming download.
    """
    cover_html = f"""
    <html>
    <head>
      <style>
        body {{ font-family: 'Georgia', serif; margin: 40px; color: #1a1a1a; }}
        .cover {{ text-align: center; padding-top: 120px; }}
        .title {{ font-size: 28px; font-weight: bold; margin-bottom: 16px; }}
        .query {{ font-size: 16px; color: #555; font-style: italic; margin-bottom: 32px; }}
        .badge {{ display: inline-block; padding: 4px 12px; border-radius: 4px; font-size: 12px; margin: 4px; }}
        .verified {{ background: #d1fae5; color: #065f46; }}
        .contested {{ background: #fef3c7; color: #92400e; }}
        .unverified {{ background: #f3f4f6; color: #374151; }}
        .meta {{ font-size: 12px; color: #888; margin-top: 48px; }}
        .claim-tag-v {{ color: #065f46; font-weight: bold; font-size: 11px; }}
        .claim-tag-c {{ color: #92400e; font-weight: bold; font-size: 11px; }}
        .claim-tag-u {{ color: #6b7280; font-weight: bold; font-size: 11px; }}
      </style>
    </head>
    <body>
      <div class="cover">
        <div style="font-size:14px; letter-spacing:4px; color:#888; margin-bottom:24px;">
          VERITY RESEARCH REPORT
        </div>
        <div class="title">Research Report</div>
        <div class="query">"{query}"</div>
        <div>
          <span class="badge verified">✓ {stats.get('verified_count', 0)} Verified</span>
          <span class="badge contested">⚡ {stats.get('contested_count', 0)} Contested</span>
          <span class="badge unverified">? {stats.get('unverified_count', 0)} Unverified</span>
        </div>
        <div class="meta">
          Generated by Verity · {datetime.now().strftime('%B %d, %Y')} ·
          {stats.get('total_sources', 0)} sources analyzed
        </div>
      </div>
      <div style="page-break-after: always;"></div>
      {report_html}
    </body>
    </html>
    """

    pdf_bytes = HTML(string=cover_html).write_pdf()
    return pdf_bytes
```

### New route

```python
from fastapi.responses import StreamingResponse
from services.pdf_exporter import generate_pdf
import io

@app.get("/export/{job_id}/pdf")
async def export_pdf(job_id: str):
    job = job_store.get(job_id)
    if not job or not job.get("report_html"):
        return {"error": "Report not ready"}

    pdf_bytes = generate_pdf(
        report_html=job["report_html"],
        query=job["query"],
        stats=job.get("stats", {})
    )

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=verity-report-{job_id[:8]}.pdf"}
    )
```

---

## 9. SPEED OPTIMIZATIONS

### Optimization 1 — Crawler concurrency hint
LangGraph `Send()` already parallelizes crawlers. Add this to ensure
asyncio doesn't serialize the search calls inside each crawler:

```python
# In agents/crawler.py
import asyncio

async def run_crawler_async(sub_question, angle):
    """Use async search clients where available."""
    results = await route_and_search(angle, sub_question["question"])
    return extract_claims(results, sub_question)
```

### Optimization 2 — Dedup batch upsert
Instead of upserting one vector at a time, batch:

```python
# In agents/dedup.py — batch upsert
vectors_batch = []
for finding in findings:
    emb = get_embedding(finding["claim"])
    vectors_batch.append({"id": finding["id"], "values": emb, "metadata": {...}})

# One API call instead of N
index.upsert(vectors=vectors_batch, batch_size=100)
```

### Optimization 3 — Parallel Devil's Advocate
Devil's Advocate searches run in parallel, one per VERIFIED claim:

use both tools
```python
# agents/devil_advocate.py
from langchain_core.tools import tool
from firecrawl import FirecrawlApp
from tavily import TavilyClient
from langgraph.prebuilt import create_react_agent
from services.llm_factory import get_reasoning_llm

tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
firecrawl = FirecrawlApp(api_key=os.getenv("FIRECRAWL_API_KEY"))  # already exists

@tool
def tavily_search(query: str) -> str:
    """Search academic papers and news for counterevidence."""
    results = tavily.search(query, search_depth="advanced", max_results=5)
    return str(results.get("results", []))

@tool
def firecrawl_search(query: str) -> str:
    """Search forums, Reddit and practitioner sites for counterevidence."""
    results = firecrawl.search(query, limit=4)
    return str(results.get("data", []))

# Both tools — no new API keys needed
tools = [tavily_search, firecrawl_search]

llm = get_reasoning_llm()

# create_react_agent handles the tool-call loop automatically
devil_advocate_agent = create_react_agent(llm, tools)
```

```python
# In graph/graph.py — add Devil's Advocate as Send() fan-out
def route_devil_advocate(state: ResearchState):
    verified = [r for r in state["conflict_reports"] if r["verdict"] == "VERIFIED"]
    return [Send("devil_advocate", {"claim": r}) for r in verified]

builder.add_conditional_edges("conflict_agent", route_devil_advocate, ["devil_advocate"])
```

### Optimization 4 — Skip Synthesis until all parallel agents done
Use LangGraph's implicit synchronization — Synthesis node only fires
when ALL Devil's Advocate Send() nodes have completed.

---

## 10. UPDATED PYPROJECT.TOML (V2)

```toml
[project]
name = "verity"
version = "2.0.0"
description = "Deep research intelligence agent with source conflict detection"
requires-python = ">=3.11"

dependencies = [
    # Orchestration
    "langgraph>=0.2.0",
    "langchain-google-genai>=1.0.0",
    "langsmith>=0.1.0",

    # Google AI
    "google-generativeai>=0.8.0",

    # Web research
    "firecrawl-py>=1.0.0",
    "tavily-python>=0.3.0",

    # Vector DB
    "pinecone-client>=3.0.0",
    "openai>=1.0.0",        # for text-embedding-3-small only

    # Citation graph
    "networkx>=3.2",

    # Backend
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.29.0",
    "python-dotenv>=1.0.0",
    "pydantic>=2.0.0",
    "httpx>=0.27.0",

    # PDF export
    "weasyprint>=60.0",
]
```

---

## 11. UPDATED .env.example (V2)

```bash
# Google AI (replaces Anthropic)
GOOGLE_API_KEY=AIza...
GEMINI_REASONING_MODEL=gemini-3.1-pro-preview
GEMINI_FAST_MODEL=gemini-2.5-flash-preview-09-2025

# Web Research
FIRECRAWL_API_KEY=fc-...
TAVILY_API_KEY=tvly-...

# Vector DB
PINECONE_API_KEY=...
PINECONE_INDEX_NAME=verity-findings

# Embeddings (for Pinecone)
OPENAI_API_KEY=...     # text-embedding-3-small only, no completions

# Observability
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__...
LANGCHAIN_PROJECT=verity-v2
```

---

## 12. BUILD ORDER FOR V2

Follow this sequence. Do not skip steps.

1. **Migrate LLM** — swap Anthropic imports to Gemini, write `llm_factory.py`, test one agent call
2. **Add FactGuard** — write `factguard.py`, wire into Crawler and Conflict agents
3. **Source Router** — write `source_router.py`, update Crawler to use it, verify Tavily + Firecrawl both fire
4. **Fix streaming** — update Synthesis to stream, update SSE route, update frontend token consumer
5. **NetworkX Citation Graph** — write `citation_graph.py`, write Citation Graph Agent, add `/graph/{job_id}` route
6. **Devil's Advocate** — write agent + prompt, wire into graph after Conflict Detector via `Send()`
7. **PDF Export** — write `pdf_exporter.py`, add export route, add download button to frontend
8. **Speed optimizations** — batch Pinecone upsert, async crawlers, parallel Devil's Advocate
9. **Engineered prompts** — replace all V1 prompts with the V2 prompts from Section 6
10. **D3.js Citation Graph UI** — render the NetworkX JSON output as force-directed graph

---

*End of CLAUDE_V2.md — hand this to Claude Code alongside CLAUDE.md (V1) and say:
"Read both files. V2 supersedes V1 where they conflict. Start with Step 1: LLM migration."*
