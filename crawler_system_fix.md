# QMA Crawler System Instructions — v3.0
# Scope: Crawler Layer Rebuild (Iterative Fix Phase 1)
# Rule: Read fully before editing any file. All changes are ADDITIVE.
# Rule: Do not remove any existing robustness logic — only fix and extend.

---

## OVERVIEW

This document governs the crawler layer exclusively.
The crawler layer is the most critical component of the deep research pipeline.
Everything downstream — dedup, conflict, synthesis, citation graph, badges — is
only as good as what the crawlers bring in.

The goal: **pull the maximum amount of high-quality, temporally-accurate data
from the most reliable sources, validate every batch with Gemini, and never
fail silently.**

Primary tools: Firecrawl, Tavily
Validation tool: Gemini (batch source validation only — not a search provider)
Fallback tool: Gemini Google Search Grounding (partial, recency rescue only)

---

## SECTION 1 — CRAWLER TOOL HIERARCHY

### Primary Crawlers (Always Active)

**Firecrawl** — the backbone crawler
- Use for: full page scraping, structured content extraction, deep research,
  historical data (up to 1 year), JS-rendered pages, paywalled content attempts
- Strengths: full page content, clean markdown extraction, JS rendering,
  `onlyMainContent` filtering, configurable wait times for slow pages
- Always run first for deep research queries
- For breaking news, run in parallel with Tavily

**Tavily** — the real-time discovery engine
- Use for: real-time search, citation-ready snippets, breaking news,
  fast URL discovery, news aggregation
- Strengths: structured citations, fast (< 5s), built for AI pipelines,
  recency-aware scoring
- Always supplement Firecrawl results
- For breaking news, run in parallel with Firecrawl

### Validation Layer (Always Active — Not a Crawler)

**Gemini** — batch source validator
- Use for: validating every batch of crawled sources after collection
- Validates: source credibility, content relevance to query, date accuracy,
  factual plausibility, duplicate intent detection
- Never used as a search or crawl provider in the primary flow
- Runs after every crawler batch, before dedup

### Fallback (Breaking News Rescue Only)

**Gemini Google Search Grounding**
- Activates ONLY when: both Firecrawl and Tavily return fewer than 2 sources
  AND query is classified as "breaking" (< 24 hours)
- Sources from this fallback are tagged `is_partial: true` and
  `provider: gemini_grounding`
- These sources can only contribute to PARTIALLY VERIFIED badges — never VERIFIED
- Do NOT activate for deep research or historical queries under any circumstances

### Other Scrapers — Decision

Do NOT add more primary scrapers. Reason: every additional scraper introduces
a new source schema, new dedup complexity, new failure modes, and new
maintenance overhead. The system becomes brittle, not robust.

If Firecrawl and Tavily are not enough for a specific edge case,
solve it with better query engineering first (see Section 4 — Query Engineering).

> Example: Instead of adding a news-specific scraper for breaking stories,
> instruct Tavily with `search_depth: "advanced"` and `topic: "news"` params.
> Instead of adding an academic scraper, instruct Firecrawl to target
> Google Scholar URLs directly with `waitFor: 3000`.

---

## SECTION 2 — DYNAMIC TEMPORAL CONTEXT (MANDATORY)

The crawler must never use hardcoded dates, years, or timestamps anywhere.
Every temporal boundary must be derived from the system clock at runtime.

### How It Works

At the start of every crawler run, the decomposer passes two values:
- `query_type`: either "breaking" (< 24h) or "deep" (up to 1 year)
- `lookback_hours`: integer, derived from query_type, never hardcoded

| Query Type | lookback_hours | When to Use |
|---|---|---|
| breaking | 6 to 24 | Events from hours or today |
| deep | 168 to 8760 | Research spanning days to 1 year |

The crawler uses `lookback_hours` to compute a dynamic cutoff timestamp
at runtime and filters out any source published before that cutoff.

> Example of correct behavior:
> User asks: "What happened with the Claude Code leak today?"
> Decomposer classifies: breaking, lookback_hours = 6
> Crawler computes cutoff: (current system time) minus 6 hours
> Any source older than that cutoff is discarded immediately
>
> User asks: "History of Anthropic security vulnerabilities"
> Decomposer classifies: deep, lookback_hours = 8760
> Crawler computes cutoff: (current system time) minus 8760 hours (1 year)
> Sources up to 1 year old are accepted

### Date Filtering Rules

- Filter is applied AFTER scraping, not before — do not limit what you fetch,
  but validate dates on every result before passing downstream
- If a source has no detectable publish date, attempt to extract it from
  the page content or URL structure
- If date is still undetectable after extraction attempts, tag as
  `date_unknown: true` and include it — do not discard silently
- Date-unknown sources are flagged in the Gemini validation step

---

## SECTION 3 — MAXIMUM DATA PULL STRATEGY

The crawler should pull the maximum number of usable sources on every run.
"Maximum" means both quantity AND quality — more sources that pass validation,
not just raw volume.

### Per-Run Source Targets

| Query Type | Minimum Sources | Target Sources | Hard Cap |
|---|---|---|---|
| breaking | 4 | 8 to 12 | 20 |
| deep | 6 | 12 to 20 | 30 |

### Strategies to Maximize Pull

**1. Multi-angle query decomposition**
Never run a single search query. The decomposer must generate at least
3 query variations per intent before handing off to crawlers.

> Example:
> Original query: "Claude Code KAIROS daemon leak March 31 2026"
> Variation 1 (direct): "Claude Code source map npm leak 2026"
> Variation 2 (broader): "Anthropic Claude Code security vulnerability npm package"
> Variation 3 (contextual): "Claude Code open source GitHub instructkr"
> All 3 run simultaneously across Firecrawl and Tavily

**2. Source type diversification**
Each crawler run must attempt to pull from multiple source types:
- Primary news sources (tech journalism)
- Developer communities (GitHub issues, Reddit, HackerNews)
- Official sources (vendor blogs, changelogs, security advisories)
- Academic or research sources (for deep queries)

Firecrawl handles direct URL scraping for all types.
Tavily handles discovery across all types via search.

**3. Retry with broadening**
If the initial run returns fewer than the minimum threshold:
- First retry: remove the most specific term from all query variations
- Second retry: remove the second most specific term
- After 2 retries: pass what was found to Gemini validation,
  flag the report as `low_coverage: true` in the synthesis output

> Example of broadening sequence:
> Attempt 1: "Claude Code KAIROS daemon leaked npm sourcemap March 31"
> Attempt 2 (retry 1): "Claude Code KAIROS leaked npm 2026"
> Attempt 3 (retry 2): "Claude Code source code leak 2026"

**4. Parallel execution always**
All crawler instances run simultaneously. Never run crawlers sequentially.
A 4-crawler parallel run should complete in the time of a single crawler run.
Each crawler gets a subset of the query variations — no two crawlers
run the exact same query.

**5. Per-crawler timeout with partial results**
Each crawler has a 45-second hard timeout.
If timeout is hit, return whatever was collected up to that point.
Never hang. Never block sibling crawlers.
Log the partial result with `status: partial_timeout`.

---

## SECTION 4 — QUERY ENGINEERING FOR CRAWLERS

Good queries are the single highest-leverage improvement to crawler output.
These rules govern how queries are formed before being passed to crawlers.

### Query Anatomy

Every search query passed to Firecrawl or Tavily must have:
- A core entity (what is being researched)
- A temporal anchor (when — derived dynamically from lookback_hours)
- An intent qualifier (what aspect: security, legal, technical, business)

> Example of well-formed query:
> Core: "Anthropic Claude Code source map"
> Temporal: "2026" (derived, not hardcoded — computed from current year at runtime)
> Intent: "npm package leak security"
> Final: "Anthropic Claude Code source map npm package leak security 2026"

### Query Variation Rules

- Minimum 3 variations per user query
- Variations must differ in specificity, not just word order
- One variation should always be broad enough to catch adjacent coverage
- One variation should always target official/primary sources explicitly

### Firecrawl-Specific Query Tips

- Use `onlyMainContent: true` to strip navigation, ads, and boilerplate
- Use `waitFor: 2000` for JS-heavy pages (React/Next.js apps, GitHub, Reddit)
- Use `formats: ["markdown", "links"]` to get both content and outbound links
  for graph-building purposes (used later in citation graph construction)
- For GitHub repos and issues, target the URL directly rather than searching

> Example of correct Firecrawl config for a GitHub target:
> URL: "https://github.com/instructkr/claude-code"
> Options: onlyMainContent=true, waitFor=2000, formats=["markdown","links"]

### Tavily-Specific Query Tips

- Use `search_depth: "advanced"` for research queries — slower but more thorough
- Use `topic: "news"` for breaking queries
- Use `include_domains` to target high-credibility sources when known
- Use `max_results: 10` minimum per query for deep research

---

## SECTION 5 — GEMINI BATCH VALIDATION

After every crawler batch completes, every collected source must be validated
by Gemini before being passed to the dedup agent.

This is the quality gate. It replaces manual credibility scoring.

### What Gemini Validates Per Source

For each source in the batch, Gemini checks:
1. **Relevance**: Is this source actually about the research query?
2. **Date accuracy**: Does the claimed publish date match the content?
3. **Credibility tier**: Primary source, secondary source, or low-credibility?
4. **Content quality**: Is there substantive content or just a headline?
5. **Duplicate intent**: Does this source say the same thing as another in the batch?

### Gemini Validation Output Per Source

Each source gets a validation stamp:
- `validation_status`: one of `pass`, `flag`, `reject`
- `credibility_tier`: one of `primary`, `secondary`, `low`
- `relevance_score`: float from 0.0 to 1.0
- `validation_notes`: short string explaining flags or rejections

### Validation Rules

- `reject` sources are dropped immediately and logged
- `flag` sources are passed downstream but tagged — synthesis agent
  treats them with lower confidence
- Only `pass` sources with `relevance_score >= 0.6` contribute to VERIFIED badges
- The validation runs as a single batch call — do not call Gemini once per source
- Batch all sources from all crawlers into one validation call for efficiency

> Example of what a flagged source looks like downstream:
> Source URL: "https://example.com/old-article"
> validation_status: "flag"
> credibility_tier: "secondary"
> relevance_score: 0.52
> validation_notes: "Date mismatch — article claims March 2026 but content references 2024 events"
> Result: passed to dedup agent, tagged, cannot contribute to VERIFIED badge

---

## SECTION 6 — FAILURE HANDLING & LOGGING

Every failure in the crawler layer must be explicit, typed, and logged.
Silent failures are the root cause of the system producing 2 sources from
4 parallel crawlers.

### Failure Types

| Failure | Type | Behavior |
|---|---|---|
| Provider timeout | `CrawlerTimeoutError` | Return partial results, log with duration |
| Below minimum sources | `CrawlerInsufficientSourcesError` | Trigger retry sequence (Section 3) |
| Provider API error | `CrawlerProviderError` | Log provider + error code, try other provider |
| All providers failed | `CrawlerTotalFailureError` | Activate Gemini Grounding fallback if breaking, else halt |
| Date filter removed all results | `CrawlerNoValidDatesError` | Widen lookback_hours by 2x and retry once |

### Structured Log Format

Every crawler agent must emit this log entry on completion:

```
agent_name: crawler_agent_{n}
provider: firecrawl | tavily | gemini_grounding
query_used: string
input_query_count: integer
sources_fetched: integer
sources_after_date_filter: integer
sources_after_gemini_validation: integer
duration_ms: integer
status: success | partial_timeout | retry_triggered | failed
retry_count: integer
error: string or null
```

Any run with `sources_after_gemini_validation = 0` is a hard failure regardless
of `sources_fetched`. Log it as failed, do not pass empty results downstream.

---

## SECTION 7 — WHAT GOOD OUTPUT LOOKS LIKE

Before pushing to production, a crawler run on a breaking news query
(e.g., same-day event) must be able to demonstrate:

- At least 4 sources collected and validated
- Sources include at least 2 different source types
  (e.g., not all from the same domain)
- All sources published within the lookback window
- Gemini validation completed with at least 2 sources passing at `pass` status
- Total crawler layer duration under 60 seconds
- No silent zero-result returns
- Structured log emitted for every crawler agent instance

If any of these are not met, the crawler layer is not production-ready.
Fix the specific failure type from Section 6 before pushing.

---

## SECTION 8 — PRODUCTION HARDENING CHECKLIST (CRAWLER LAYER ONLY)

Run these before merging the crawler layer changes:

- [x] Grep entire crawler codebase for hardcoded years, months, or date strings
      → FIXED: SEARCH_OPTIMIZER_PROMPT no longer bans year numbers; year injected dynamically via CURRENT_YEAR
- [x] Confirm `lookback_hours` is passed as a parameter, never computed inside crawler
      → Already correct — passed from ResearchState
- [x] Confirm all 4 crawler instances run via async parallel execution
      → Handled by LangGraph Send() — no change needed
- [x] Confirm each crawler has a 45-second timeout returning partial results
      → asyncio.wait_for(45.0) wraps the gather over 3 parallel query variations
- [ ] Confirm Gemini validation runs as a single batch call after all crawlers complete
      → NOT YET IMPLEMENTED — current factguard is rule-based only. Gemini batch validation
         is a Phase 2 task after crawler layer is producing sufficient sources.
- [x] Confirm retry with broadened query triggers when < 4 valid_pages
      → Retry uses broadened primary_query, appends new unique pages
- [x] Confirm structured log is emitted on every run including failures
      → AGENT_LOG line emitted at end of every run with query_variations, sources_fetched, findings counts
- [ ] Run a live test: query about an event from 3 hours ago
      Assert: sources_after_gemini_validation >= 4
- [ ] Run a live test: query about a topic from 6 months ago
      Assert: sources_fetched >= 10, sources_after_date_filter >= 6
- [ ] Confirm Gemini Grounding fallback only activates for breaking queries
      with fewer than 2 sources after all retries

## IMPLEMENTED CHANGES (Phase 1)

| File | Change | Reason |
|------|--------|--------|
| `agents/crawler.py` | `PAGE_CONTENT_CAP` 800 → 2500 | 800 chars = 2 sentences; LLM couldn't extract real claims |
| `agents/crawler.py` | 3 parallel query variations instead of 1 | Single query yielded too few sources; spec mandates 3 variations |
| `agents/prompts/crawler.py` | `SEARCH_OPTIMIZER_PROMPT` now outputs JSON array of 3 variants | Enables multi-query execution in crawler_node |
| `agents/prompts/crawler.py` | Removed "Do NOT add year numbers" rule | Contradicted spec Section 4 temporal anchor requirement |
| `services/source_router.py` | `practitioner` angle: Firecrawl-only → both Tavily + Firecrawl | Firecrawl search returns short snippets; Tavily returns full raw_content |
| `agents/prompts/decomposer.py` | Granular lookback_hours: [6,24,168,720,2160,8760] | Binary 6/8760 was too coarse — "last week" queries got 1-year window |
| `agents/prompts/decomposer.py` | Removed hardcoded "2022–2025" year ranges | Static year strings age badly; replaced with relative phrasing |

---

## APPENDIX — SUGGESTED ADDITIONAL SCRAPER (EVALUATION ONLY)

These are options if Firecrawl + Tavily ever hit a hard ceiling in specific domains.
Do NOT integrate any of these now. Evaluate only after production data shows
a specific gap that query engineering cannot solve.

| Tool | Strength | When to Consider |
|---|---|---|
| Exa.ai | Neural search, embedding-based retrieval | If semantic search gaps appear for research queries |
| Jina Reader | Clean markdown from any URL, free tier | If Firecrawl costs become a bottleneck |
| Apify | Complex site automation, anti-bot bypass | If specific high-value sites block Firecrawl consistently |
| SerpAPI | Google SERP structured data | If Tavily misses Google-indexed sources specifically |

Integration rule: any new scraper must conform to the same source schema
(`url, title, publish_date, content, credibility_score, provider`)
and must be validated by the same Gemini batch validation step.
Adding a scraper that bypasses the validation gate is not permitted.

