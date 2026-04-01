# QMA Instructions — Phase 3: Synthesis Agent + Citation Graph
# Iterative Fix Series | Depends on: Phase 1 + Phase 2 verified and merged
# Scope: synthesis_agent and citation_graph ONLY
# publisher_agent has been removed from the pipeline — do not reference it
# Rule: Read fully before editing. All changes are ADDITIVE.
# Rule: Do not touch crawler.py, dedup.py, conflict.py, graph.py — locked.

---

## CONTEXT

After Phase 1 and Phase 2, the pipeline now delivers:
- 4–20 validated, deduplicated sources
- Extracted claims with conflict flags and structured output
- Correctly forwarded lookback_hours and query_type

Phase 3 fixes the final two broken outputs:
1. synthesis_agent — missing badge logic, missing metadata counts,
   missing citation_graph field, and no structured output contract
2. citation_graph — backend must build it, retry if empty,
   and the frontend must render it correctly

---

## SECTION 1 — SYNTHESIS AGENT: INPUT

synthesis_agent receives one object from the orchestrator.
All three keys must be present. Handle missing keys gracefully — never crash.

```
{
  "query": "string",
  "query_type": "breaking | deep",
  "lookback_hours": integer,
  "sources": [...from dedup_agent...],
  "claims": [...from conflict_agent...],
  "conflicts": [...from conflict_agent...]
}
```

- If `sources` is empty → raise SynthesisEmptySourcesError, halt
- If `claims` is empty but `sources` is not → synthesis_agent extracts
  claims directly from source content itself (fallback path)
- If `conflicts` is empty → valid, proceed without conflict notes

---

## SECTION 2 — SYNTHESIS AGENT: 4-TIER BADGE LOGIC

Badges are computed here and only here. No other agent assigns them.

### Tier Rules

```
VERIFIED
  → 3 or more sources support the claim
  AND all supporting sources have validation_status = "pass"
  AND sources are from 3 different root domains (independence rule)
  AND no hard_conflict flagged against this claim

PARTIALLY_VERIFIED
  → Exactly 2 independent sources support the claim
  OR any supporting source has provider = "gemini_grounding"
  OR claim has a soft_conflict flag

UNVERIFIED
  → Only 1 source supports the claim
  AND that source has validation_status = "pass"

UNSUPPORTED
  → Zero sources support the claim
  OR claim is inferred with no source backing
  OR supporting source has validation_status = "reject"
```

### Independence Rule for VERIFIED
3 sources must come from 3 different root domains.
Two articles from the same domain = 1 independent source, not 2.

> Example:
> techcrunch.com + reddit.com + github.com = 3 independent → VERIFIED ✅
> techcrunch.com + techcrunch.com + github.com = 2 independent → PARTIALLY_VERIFIED

---

## SECTION 3 — SYNTHESIS AGENT: OUTPUT CONTRACT

Every field below is required. Use null for optional fields — never omit the key.

```
{
  "executive_summary": "string — 2–4 sentences, key finding + confidence",

  "claims": [
    {
      "claim": "string — one specific, falsifiable statement",
      "badge": "VERIFIED | PARTIALLY_VERIFIED | UNVERIFIED | UNSUPPORTED",
      "supporting_sources": ["url1", "url2"],
      "contradicting_sources": ["url1"],
      "conflict_note": "string or null",
      "claim_type": "factual | causal | temporal | quantitative"
    }
  ],

  "open_questions": ["string"],

  "metadata": {
    "sources_crawled": integer,
    "sources_after_dedup": integer,
    "sources_used_in_claims": integer,
    "claims_total": integer,
    "claims_verified": integer,
    "claims_partially_verified": integer,
    "claims_unverified": integer,
    "claims_unsupported": integer,
    "conflicts_detected": integer,
    "query_type": "breaking | deep",
    "lookback_hours": integer,
    "low_coverage": boolean,
    "generated_at": "ISO8601 UTC from system clock — never hardcoded"
  },

  "citation_graph": { ...see Section 4... }
}
```

All metadata integers must be computed from actual data, not estimated.
`generated_at` must always be runtime system clock — grep for any hardcoded date.

---

## SECTION 4 — CITATION GRAPH: BACKEND LOGIC

### Structure

```
"citation_graph": {
  "nodes": [
    {
      "id": "src_{index}",
      "url": "string",
      "title": "string",
      "domain": "string — root domain only, e.g. techcrunch.com",
      "provider": "firecrawl | tavily | gemini_grounding",
      "publish_date": "string or null",
      "badge_contribution": "VERIFIED | PARTIALLY_VERIFIED | UNVERIFIED | none",
      "is_partial": boolean,
      "credibility_tier": "primary | secondary | low",
      "node_color": "string — hex code, see color rules below",
      "node_border": "string or null — hex code for special cases"
    }
  ],
  "edges": [
    {
      "claim_index": integer,
      "source_id": "src_{index}",
      "relationship_type": "supports | contradicts",
      "claim_text_preview": "string — first 80 chars of the claim"
    }
  ],
  "graph_generated": boolean,
  "generation_note": "string or null"
}
```

### Node Color Rules (backend sets these — frontend just reads them)
```
VERIFIED           → node_color = "#22c55e"
PARTIALLY_VERIFIED → node_color = "#eab308"
UNVERIFIED         → node_color = "#f97316"
UNSUPPORTED / none → node_color = "#ef4444"
gemini_grounding   → node_border = "#fbbf24" (add on top of color)
```

### Retry Logic (currently missing — add this)

After building the initial graph:
- If nodes is empty AND sources list is not empty:
  → Retry once — create one node per source and one edge linking
    each source to the executive summary as a fallback claim
  → Set generation_note = "Fallback graph — direct source mapping used"
- If after retry nodes is still empty:
  → Set graph_generated = false
  → Set generation_note = "No source-claim relationships found"
- Never set citation_graph to null — always emit the full object
  with graph_generated = false and an empty nodes/edges list if needed

---

## SECTION 5 — CITATION GRAPH: FRONTEND RENDER

The frontend graph component reads citation_graph.nodes and citation_graph.edges
directly from the synthesis output. Use React Flow, vis.js, or D3 force-directed.

### Render Rules

- Each node renders as a circle, color from `node_color` field
- If `node_border` is present, add a 3px border in that color
- Each edge renders as a line between claim node and source node:
  - `supports` → solid line
  - `contradicts` → dashed line, red tint
- Claim nodes (one per unique claim_index) render as squares,
  color based on the badge of that claim
- Source nodes render as circles

### When graph_generated = false
Render a placeholder panel:
"Citation graph unavailable — insufficient source-claim links found"
Do NOT hide the panel — always show it, even as a placeholder

### Conditional Rendering
- If citation_graph field is missing entirely from synthesis output:
  show placeholder (do not crash)
- If nodes.length = 0: show placeholder
- If nodes.length >= 1 and edges.length >= 1: render the graph

---

## SECTION 6 — UI: SOURCE COUNT DISPLAY

The report header must show dynamic counts from metadata — never hardcoded.

Format:
```
"{sources_crawled} sources crawled · {sources_used_in_claims} sources used · {claims_verified} verified"
```

> Example: "12 sources crawled · 9 sources used · 4 verified"

Badge legend below header — all 4 tiers always shown including zeros:
```
✓ VERIFIED: {claims_verified}
~ PARTIALLY VERIFIED: {claims_partially_verified}
? UNVERIFIED: {claims_unverified}
✗ UNSUPPORTED: {claims_unsupported}
```

---

## SECTION 7 — PRODUCTION CHECKLIST (PHASE 3)

- [ ] synthesis_agent raises SynthesisEmptySourcesError on empty sources
- [ ] synthesis_agent falls back to direct claim extraction when claims input is empty
- [ ] Independence rule applied — 3 different root domains required for VERIFIED
- [ ] citation_graph field always present in output (never null or missing)
- [ ] citation_graph retry logic fires when nodes empty but sources not empty
- [ ] node_color and node_border fields set on every node by backend
- [ ] graph_generated = false renders placeholder panel in frontend, not crash
- [ ] Report header shows dynamic metadata counts (not hardcoded "2 sources")
- [ ] All 4 badge tier counts in legend including zeros
- [ ] metadata.generated_at uses system clock — grep confirms no hardcoded dates
- [ ] Live test: 8+ source query → at least one VERIFIED badge appears
- [ ] Live test: conflicting source query → conflict_note visible on claim row
- [ ] Live test: citation graph renders with nodes + edges
- [ ] Live test: "2 sources crawled · 2 claims extracted" is gone from UI

