# QMA Instructions — Phase 4: Devil's Advocate Agent + Final Smoke Test
# Iterative Fix Series | Depends on: Phase 1 + Phase 2 + Phase 3 verified and merged
# Scope: devil_advocate agent activation + adversarial crawl + smoke test
# Rule: Read fully before editing. All changes are ADDITIVE.
# Rule: Do not touch crawler.py, dedup.py, conflict.py, synthesis.py — locked.

---

## CONTEXT — WHY DEVIL'S ADVOCATE IS DEAD

From the original trace, `route_to_devil_advocate` ran at 0.00s.
The routing condition to activate it was gated on claims count or
badge threshold — both were always 0 before Phase 3.
Now that synthesis produces real VERIFIED and PARTIALLY_VERIFIED claims,
the Devil's Advocate has meaningful targets to challenge.

What Devil's Advocate must do:
- Take the strongest VERIFIED claims from synthesis output
- Run a second targeted crawl using adversarially-framed queries
- Find counter-evidence, rebuttals, corrections, or denied reports
- Return a structured challenge list back to synthesis
- Synthesis incorporates challenges into final badge decisions and conflict_notes

---

## SECTION 1 — ROUTING FIX (graph.py or orchestrator)

### Current Broken Behavior
`route_to_devil_advocate` activates on a condition that was never satisfied.
Fix the routing condition to activate when:

```
len(verified_claims) >= 1
OR len(partially_verified_claims) >= 2
```

If neither condition is met (e.g., all claims are UNVERIFIED or UNSUPPORTED),
Devil's Advocate is skipped with a clean log:
```
status: skipped
skip_reason: "no_verified_claims_to_challenge"
```

This is a valid skip — do not treat it as a failure.

Devil's Advocate runs AFTER synthesis produces its first-pass output
and BEFORE synthesis finalizes the report. The flow is:

```
conflict_agent output
      ↓
synthesis_agent (first pass — produces draft badges + claims)
      ↓
route_to_devil_advocate (checks if VERIFIED claims exist)
      ↓
devil_advocate_agent (adversarial crawl + challenge generation)
      ↓
synthesis_agent (second pass — incorporates challenges, finalizes badges)
      ↓
publisher output
```

---

## SECTION 2 — DEVIL'S ADVOCATE AGENT: ADVERSARIAL CRAWL

### What It Does
Takes each VERIFIED claim and generates an adversarially-framed search query
designed to find counter-evidence, denials, corrections, or alternative interpretations.

### Adversarial Query Framing Rules

For each VERIFIED claim, generate exactly 2 adversarial queries:
- Query 1: Challenge framing — looks for denial, rebuttal, or correction
- Query 2: Alternative explanation framing — looks for a different cause or interpretation

> Example:
> Verified claim: "Claude Code source was leaked via npm source map on March 31 2026"
>
> Adversarial Query 1 (challenge):
> "Anthropic Claude Code leak denied response official statement April 2026"
>
> Adversarial Query 2 (alternative):
> "Claude Code npm source map intentional open source release not leak"

### Crawl Configuration
- Use Tavily only for adversarial crawl — it is faster and recency-aware
- Use `search_depth: "advanced"` and `topic: "news"` for all adversarial queries
- Run all adversarial queries in parallel via asyncio.gather
- Per-query timeout: 20 seconds (shorter than primary crawl — this is supplementary)
- Minimum adversarial sources needed: 1 per VERIFIED claim attempted
- If 0 adversarial sources found for a claim: log it, move on, do not block

---

## SECTION 3 — DEVIL'S ADVOCATE AGENT: CHALLENGE OUTPUT

For each VERIFIED claim that was challenged, produce a challenge object:

```
{
  "original_claim": "string",
  "original_badge": "VERIFIED | PARTIALLY_VERIFIED",
  "challenge_found": boolean,
  "challenge_strength": "strong | moderate | weak | none",
  "counter_evidence": [
    {
      "url": "string",
      "title": "string",
      "excerpt": "string — the specific passage that challenges the claim",
      "challenge_type": "denial | correction | alternative_explanation | conflicting_data"
    }
  ],
  "recommended_badge_change": "VERIFIED | PARTIALLY_VERIFIED | UNVERIFIED | no_change",
  "challenge_note": "string — one sentence explaining the challenge"
}
```

### Challenge Strength Rules

```
strong   → direct denial or correction from a primary source
           (official statement, original author, regulator)
           → recommended_badge_change: PARTIALLY_VERIFIED (downgrade)

moderate → credible alternative explanation from a secondary source
           → recommended_badge_change: PARTIALLY_VERIFIED (downgrade)

weak     → speculative or low-credibility counter-claim
           → recommended_badge_change: no_change
           → add challenge_note to claim's conflict_note in synthesis

none     → no counter-evidence found
           → recommended_badge_change: no_change
           → this actually strengthens the original VERIFIED badge
```

---

## SECTION 4 — SYNTHESIS AGENT: SECOND PASS (CHALLENGE INCORPORATION)

synthesis_agent receives devil_advocate output and does a second pass.
This is a targeted update — not a full regeneration.

### What Changes in Second Pass

For each claim that has a challenge object:

1. If `challenge_strength = "strong"` or `"moderate"`:
   - Apply `recommended_badge_change` to the claim's badge
   - Append `challenge_note` to the claim's `conflict_note` field
   - Add `counter_evidence` URLs to `contradicting_sources`

2. If `challenge_strength = "weak"`:
   - Keep badge unchanged
   - Append `challenge_note` to `conflict_note` as a soft flag

3. If `challenge_found = false`:
   - Keep badge unchanged
   - No changes to the claim

### Second Pass Must NOT:
- Re-run claim extraction — only update existing claims
- Re-run badge independence check — already done in first pass
- Change claims that had no challenge object

After second pass, synthesis emits the final report with an additional
metadata field:
```
"devil_advocate_ran": boolean,
"claims_challenged": integer,
"claims_downgraded": integer
```

---

## SECTION 5 — STRUCTURED LOGGING

devil_advocate_agent must emit:
```
agent_name: devil_advocate_agent
claims_targeted: integer
adversarial_queries_run: integer
counter_sources_found: integer
challenges_produced: integer
claims_with_strong_challenge: integer
claims_with_no_challenge: integer
duration_ms: integer
status: success | skipped | partial | failed
skip_reason: string or null
```

Zero-latency runs on non-empty input = bug. Same CI rule as dedup and conflict.

---

## SECTION 6 — FINAL SMOKE TEST PROTOCOL

Run this after Phase 4 is merged. Two test queries — one breaking, one deep.

### Test A — Breaking News Query
Query: use a real event from the last 6 hours
Expected behavior:
- decomposer classifies: breaking, lookback_hours = 6
- 4+ sources returned from parallel Firecrawl + Tavily crawl
- dedup reduces source count (post-dedup < pre-dedup)
- conflict_agent extracts claims from deduplicated sources
- synthesis first pass produces at least 1 VERIFIED or PARTIALLY_VERIFIED claim
- devil_advocate activates, runs adversarial Tavily queries
- synthesis second pass incorporates any challenges
- final report header shows dynamic source counts
- citation graph renders with nodes + edges
- devil_advocate_ran = true in metadata

Pass criteria:
- sources_crawled >= 4
- claims_total >= 2
- at least 1 badge is not UNVERIFIED
- citation_graph.graph_generated = true
- devil_advocate_ran = true OR skip_reason = "no_verified_claims_to_challenge"
- report header does NOT say "2 sources crawled"

### Test B — Deep Research Query
Query: use a topic spanning the last 6 months
Expected behavior:
- decomposer classifies: deep, lookback_hours = 4320 (6 months)
- 8+ sources returned
- dedup, conflict, synthesis run as expected
- devil_advocate activates on VERIFIED claims
- final report has multiple badge tiers visible

Pass criteria:
- sources_crawled >= 8
- claims_total >= 4
- at least 1 VERIFIED badge (3 independent domains)
- at least 1 PARTIALLY_VERIFIED badge
- citation_graph has >= 4 nodes and >= 4 edges
- metadata.generated_at is a valid ISO8601 UTC timestamp

### If Either Test Fails
Check LangSmith trace in this order:
1. Is crawler log showing sources_after_gemini_validation >= 4?
   If not: crawler issue, go back to Phase 1 instructions
2. Is dedup log showing total_after_semantic_dedup > 0?
   If not: dedup handoff issue, check flatten_and_validate step
3. Is conflict log showing total_claims_extracted > 0?
   If not: conflict agent issue, check < 2 sources guard
4. Is devil_advocate log showing status = skipped?
   If yes and skip_reason = "no_verified_claims_to_challenge":
   synthesis badge logic issue — check _enforce_badge_independence
5. Is citation_graph.graph_generated = false?
   If yes: check fallback retry logic in synthesis.py

---

## SECTION 7 — PRODUCTION CHECKLIST (PHASE 4 + FINAL)

### Devil's Advocate
- [ ] Routing condition fixed — activates when verified_claims >= 1
- [ ] Clean skip logged when no verified claims exist
- [ ] Adversarial queries generated for each VERIFIED claim (2 per claim)
- [ ] Adversarial Tavily crawl runs in parallel
- [ ] Challenge objects produced with strength rating
- [ ] Strong/moderate challenges downgrade badge in synthesis second pass
- [ ] Weak challenges append to conflict_note only
- [ ] devil_advocate_ran, claims_challenged, claims_downgraded in metadata
- [ ] Structured log emitted on every run including skips

### Full Pipeline
- [ ] No hardcoded dates anywhere — grep confirms
- [ ] No agent runs at 0.00s on non-empty input without a skip_reason
- [ ] All 5 agents log structured entries on every run
- [ ] Smoke Test A passes all criteria
- [ ] Smoke Test B passes all criteria
- [ ] LangSmith trace shows all agents with non-zero durations
  (except valid skips with skip_reason logged)
- [ ] citation_graph renders in frontend on both test queries
- [ ] Report header shows dynamic counts on both test queries
- [ ] Badge independence downgrades visible in LangSmith
  when LLM over-assigns VERIFIED

