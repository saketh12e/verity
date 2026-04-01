# QMA Instructions — Phase 2: Dedup Agent + Conflict Agent Fix
# Iterative Fix Series | Depends on: Phase 1 (Crawler) being merged and verified
# Rule: Read fully before editing any file. All changes are ADDITIVE.
# Rule: Do not touch crawler.py, source_router.py, or decomposer.py — those are Phase 1 locked.

---

## CONTEXT — WHY THESE AGENTS ARE DEAD

From the trace, both `dedup_agent` and `conflict_agent` ran at **0.00s**.
Zero latency on non-trivial input = silent skip. This means:
- `dedup_agent` received an empty or malformed payload from the crawler merge step
- `conflict_agent` received nothing because dedup produced nothing
- `synthesis_agent` then ran on raw, unprocessed crawler output with zero deduplication
  and zero conflict detection, which is why every claim came out `? UNVERIFIED`

The fix is in two parts:
1. Fix the handoff — ensure crawler output is correctly passed to dedup_agent
2. Fix the agents themselves — add input guards, real logic, and structured output

---

## SECTION 1 — DEDUP AGENT FIX

### What It Must Do
Take the merged list of sources from all crawler agents and return a
deduplicated list where no two sources cover the same content.

### Step 1 — Fix the Handoff (Most Likely Root Cause)

The crawler merge step must produce a flat list of source objects before
passing to dedup_agent. If the crawlers return nested lists per agent
(e.g., list of lists), the dedup agent receives a malformed structure
and silently skips.

Correct handoff structure — every source must conform to this schema:
```
{
  "url": "https://...",
  "title": "string",
  "content": "string (min 100 chars, max 2500 chars)",
  "publish_date": "ISO8601 string or null",
  "provider": "firecrawl | tavily | gemini_grounding",
  "is_partial": false,
  "date_unknown": false,
  "query_variation_used": "string"
}
```

Before dedup_agent receives any input, the orchestrator must:
- Flatten all crawler results into a single list
- Remove any source with empty or null `content`
- Remove any source with empty or null `url`
- Log the count: "Pre-dedup source count: N"

If count after flattening is 0, raise `DedupEmptyInputError` and halt.
Do NOT pass an empty list to dedup_agent silently.

### Step 2 — Dedup Logic (Two Passes)

**Pass 1 — Exact URL dedup (fast)**
Hash each URL with md5. If two sources share the same hash, keep the one
with longer `content` and discard the other.
This pass must complete in under 1 second regardless of input size.

**Pass 2 — Semantic dedup (content-level)**
For sources that survived Pass 1, compare content similarity.
Use the first 500 characters of `content` for comparison — not the full text.
If two sources have cosine similarity above 0.85, they are semantic duplicates.
Keep the one with the higher credibility tier (primary > secondary > low).
If tiers are equal, keep the one with the more recent `publish_date`.

> Example of what semantic dedup catches:
> Source A: Reuters article about Claude Code leak
> Source B: A blog that copy-pasted the Reuters article with minor rewording
> URL hash: different (passes Pass 1)
> Content similarity: 0.91 (caught in Pass 2, Reuters kept, blog discarded)

### Step 3 — Output Contract

dedup_agent must emit:
```
{
  "sources": [...deduplicated source objects...],
  "total_input": integer,
  "total_after_url_dedup": integer,
  "total_after_semantic_dedup": integer,
  "duplicates_removed": integer,
  "duration_ms": integer
}
```

Minimum output: at least 2 sources. If fewer than 2 survive dedup,
log a warning `DedupLowOutputWarning` and pass all available sources through.
Never return an empty list from dedup_agent — if input had sources, output must too.

---

## SECTION 2 — CONFLICT AGENT FIX

### What It Must Do
Take the deduplicated source list from dedup_agent, extract claims,
compare them against each other, and flag any contradictions.

### Step 1 — Fix the Activation Guard

conflict_agent currently activates even when claims count is 0.
Add this guard at entry:

```
If len(sources) < 2:
  Log: "conflict_agent skipped — insufficient sources: {len(sources)}"
  Return empty conflict list (not null, not error — empty list)
  This is a valid skip, not a failure
```

If len(sources) >= 2, the agent MUST run. A 0.00s completion on 2+ sources
is a bug and must be caught in CI.

### Step 2 — Claim Extraction from Sources

Before comparing claims, conflict_agent must extract atomic factual claims
from each source's content. One source typically yields 2–5 claims.

Each extracted claim must be:
```
{
  "claim_text": "string — one specific, falsifiable statement",
  "source_url": "string — the source this came from",
  "claim_type": "factual | causal | temporal | quantitative",
  "confidence": float (0.0 to 1.0)
}
```

> Example of a good atomic claim:
> "Anthropic's Claude Code source code was leaked via an npm source map file on March 31, 2026"
> claim_type: temporal + factual, confidence: 0.9
>
> Example of a bad claim (too vague to conflict-check):
> "Claude Code has security issues"
> — Reject this during extraction. Claims must be specific and falsifiable.

### Step 3 — Conflict Detection

Compare every claim pair across different sources (not within the same source).
A conflict exists when two claims from different sources directly contradict
each other on the same subject.

Conflict severity levels:
- `hard_conflict`: direct factual contradiction (e.g., Source A says leaked, Source B says not leaked)
- `soft_conflict`: different figures or dates for the same event (e.g., different line counts for the leaked code)
- `perspective_difference`: different interpretations of the same fact (not a true conflict — flag but don't penalize)

> Example of a hard conflict:
> Claim A (from TechCrunch): "The leaked file contained 47,000 lines of code"
> Claim B (from Wired): "The leaked file contained 12,000 lines of code"
> Conflict type: hard_conflict on quantitative claim
> Result: both claims are flagged, synthesis_agent must surface this to the user

### Step 4 — Output Contract

conflict_agent must emit:
```
{
  "claims": [...all extracted claims with conflict flags added...],
  "conflicts": [
    {
      "claim_a": "string",
      "claim_b": "string",
      "source_a_url": "string",
      "source_b_url": "string",
      "conflict_type": "hard_conflict | soft_conflict | perspective_difference",
      "severity": "high | medium | low",
      "resolution_note": "string or null"
    }
  ],
  "total_claims_extracted": integer,
  "total_conflicts_found": integer,
  "duration_ms": integer
}
```

If no conflicts are found, emit `"conflicts": []` — not null, not missing field.
A clean conflict list is a valid and expected result.

---

## SECTION 3 — ORCHESTRATOR HANDOFF CHAIN (FIX THE WIRING)

The most likely reason both agents ran at 0.00s is broken wiring in the orchestrator.
The orchestrator must explicitly await each step and pass output as input to the next.

Correct execution chain:
```
Step 1: crawler_results = await asyncio.gather(crawler_1, crawler_2, crawler_3, crawler_4)
Step 2: flat_sources = orchestrator.flatten_and_validate(crawler_results)
        → Log: "Flattened source count: {len(flat_sources)}"
Step 3: dedup_output = await dedup_agent(flat_sources)
        → Log: "Post-dedup source count: {dedup_output['total_after_semantic_dedup']}"
Step 4: conflict_output = await conflict_agent(dedup_output['sources'])
        → Log: "Claims extracted: {conflict_output['total_claims_extracted']}"
        → Log: "Conflicts found: {conflict_output['total_conflicts_found']}"
Step 5: synthesis_input = {
          "sources": dedup_output['sources'],
          "claims": conflict_output['claims'],
          "conflicts": conflict_output['conflicts']
        }
Step 6: synthesis_output = await synthesis_agent(synthesis_input)
```

Each step must explicitly check its input before calling the next agent.
If any step produces zero output where non-zero is expected,
log the failure and halt with a typed error — do not continue with empty data.

---

## SECTION 4 — STRUCTURED LOGGING FOR BOTH AGENTS

Both agents must emit the same log format as the crawler agents:

```
agent_name: dedup_agent | conflict_agent
input_size: integer
output_size: integer
duration_ms: integer
status: success | skipped | failed
skip_reason: string or null (only when status=skipped)
error: string or null (only when status=failed)
```

Any agent with `input_size > 0` AND `duration_ms < 100` AND `status != skipped`
must trigger a WARNING in the log. This is the CI signal for silent skip bugs.

---

## SECTION 5 — PRODUCTION CHECKLIST (PHASE 2)

Run these before merging Phase 2 changes:

- [ ] orchestrator.flatten_and_validate() exists and logs pre-dedup count
- [ ] dedup_agent raises DedupEmptyInputError on empty input
- [ ] dedup_agent emits structured output with all count fields
- [ ] conflict_agent skips cleanly (empty list, not error) when sources < 2
- [ ] conflict_agent runs and produces claims when sources >= 2
- [ ] conflict_agent emits structured output with conflicts list (even if empty)
- [ ] orchestrator passes dedup_output['sources'] to conflict_agent explicitly
- [ ] orchestrator builds synthesis_input with sources + claims + conflicts
- [ ] All 4 log entries appear in every run (crawler, dedup, conflict, synthesis)
- [ ] Run live test: query with 6+ sources → assert dedup_output count < input count
- [ ] Run live test: query with known contradictory sources → assert conflicts > 0
- [ ] No agent completes in < 100ms on non-trivial input without a skip_reason log

