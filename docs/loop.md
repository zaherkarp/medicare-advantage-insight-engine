# Self-Improvement Loop — State

Read this first. Update it in every PR. This file is the loop's durable memory
across sessions: a fresh session resumes by reading [`docs/goal.md`](goal.md)
and this file, then entering the protocol at step (a).

## Protocol (short form)

Per iteration:

- **(a) Sync** — reset the working branch to `origin/main` (previous PR is merged).
- **(b) Measure** — run the scorecard (`scripts/scorecard.py` once it exists;
  manual queries until then) against the test suite and the published archive DB.
- **(c) Pick** — take the top item from the backlog below (impact × feasibility,
  must fit one PR). Reorder only with a recorded justification.
- **(d) Implement** — code + tests + the goal/loop doc updates, on the branch.
- **(e) Verify locally** — `ruff format` + `ruff check` + full `pytest`
  (golden set explicitly); exercise web features via the test client; build the
  static export for UI changes.
- **(f) Ship** — push, open a PR against `main`, merge when CI is green.
- **(g) On red CI** — read failing job logs, fix on the same PR, re-verify.
  After 3 failed fix rounds: close the PR, record the item under Blocked, move on.
- **(h) Record** — update the iteration log and scorecard snapshot here (in the
  PR itself), restart from (a).

Stopping conditions: owner says stop; backlog exhausted above the value bar; or
two consecutive blocked iterations (surface instead of thrashing).

## Scorecard snapshot (2026-07-03, after iteration 3)

See [`docs/goal.md`](goal.md) for metric definitions, baselines, and targets.

| S1 | S2 (P/R) | S3 floors | C1 | C2 | C3 | F1 | F2 | F3 low-yield | Q1 | Q2 | U1 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 83 | 0.914 / 0.914 | 0.90 / 0.90 | 31/31 | 8/10 | absent | 47–105 s | 4 h / 6 h | 41 of 63 | 0 / 0 | absent | 2/4 |

S2 dropped from 1.00/1.00 by design: the expanded set contains six documented
KNOWN-GAP entries the scorer currently misclassifies (see the fixture header).
Fixing those gaps — not adding easy cases — is how S2 climbs back toward 1.0.

## Iteration log

| # | Date | PR | Shipped | Scorecard delta | Notes |
|---|---|---|---|---|---|
| 0 | 2026-07-03 | — | Baseline measurement (tests, golden set, published archive, run durations) | established baselines | Feedback table is empty → exclusion mining (backlog #4) must use archive score distributions, not owner verdicts. |
| 1 | 2026-07-03 | #35 | Docs truth pass (SEC/CMS fetchers live, 6 categories, 8 tables, historical banners on pr-summary/qa-results) + `docs/goal.md` + `docs/loop.md` | U1 0/4 → 1/4 | Proved the PR → CI → merge → restart loop end to end. |
| 2 | 2026-07-03 | #36 | Golden set 20 → 83 (production stories labeled with adversarial double-review + synthetic gate traps + 6 documented known-gap entries), CI floors 0.8 → 0.9, per-entry `source_priority` in the harness, `scripts/scorecard.py` | S1 20 → 83; S3 0.8 → 0.9; S2 1.00 → 0.914 (honest hard-set measurement) | Labeling found real scorer gaps — recall: M&A sale/divestiture vocabulary, dry SEC 8-K titles, provider systems dropping MA plans, Optum absent from watched_entities; precision: ACA-marketplace/Medicaid-rule/CMS-grant stories with no MA angle score ≥ 0.33. 61 labeled false-positive examples banked for the exclusions iteration. |
| 3 | 2026-07-03 | #37 | Payer intelligence pages: `/payers` overview + `/payers/{slug}` per organization (signals, category mix, state footprint, SEC filings panel), canonical alias grouping in `payers.py` (23 groups covering all 31 watched entities, enforced by test), entity filters + stats in storage, static-export crawl, shared `_story_list.html` partial | C1 0/31 → 31/31; U1 1/4 → 2/4 | Verified end-to-end by building the static site from the production archive (23 payer pages, top payer 53 signals, panels render). Finding: production archive has **zero** SEC EDGAR stories — feeds configured but yielding nothing, likely the unset `USER_AGENT` that SEC requires; investigate in the low-yield/coverage iteration. |

## Backlog (ordered, next-up first)

| Item | Dimension | Impact | Effort | Status | Notes |
|---|---|---|---|---|---|
| 1. Parallel source fetching + cadence bump | Freshness | high | low | next | ThreadPoolExecutor in `main._fetch_all_sources`, `fetch_workers` escape hatch, per-source error isolation preserved; then crons 4h→2h (Pages), 6h→3h (alerts). |
| 2. Scoring gap fixes + `exclusions.hard`/`.soft` | Signal quality | high | med | queued | Concrete targets from iteration 2: add sale/divestiture/exit M&A vocabulary, consider Optum in watched_entities and an SEC-source boost (dry 8-K titles), and exclusions for ACA-marketplace/Medicaid-rule noise (61 labeled FP examples banked). Every change guarded by golden-set entries; fixes should lift S2 toward 1.0. |
| 3. Low-yield source review + SEC feed fix | Coverage | med | low | queued | 41 flagged sources incl. "Managed Healthcare Executive" (30 items, 0 public, max 0.06). Also: **zero** SEC EDGAR stories in production — set `USER_AGENT` in the workflows (SEC requires a descriptive UA) and verify the feeds yield. |
| 4. Near-duplicate alert suppression | Signal quality / UX | med | med | queued | Title-similarity clustering at draft time; `duplicate_of` column via guarded migration (Guardrail 3). Answers `docs/assumptions.md` open questions. |
| 5. Historical trend views | Intel depth / UX | med | med | queued | Signal volume by payer/category/week; inline SVG sparklines (static-export safe) on `/status` + payer pages. |
| 6. CMS MA enrollment data | Intel depth | high | high | queued | Monthly CPSC files → parent-org membership/share on payer pages; likely two PRs (fetch/store, then UI). |
| 7. Advisory→config automation | Meta | med | med | queued | Mined keywords → *draft* PR (never auto-merged; Guardrail 2). Only after item 2 proves out. |

## Blocked / parked

*(none yet)*

## Decision points awaiting owner

- **Semantic/LLM scoring** — would add a paid dependency to a deliberately
  free/local app (Guardrail 4). If approved: LLM as advisory re-ranker first,
  gated by the golden set.
- **Slack renderer** — needs a Slack workspace/webhook to target.
- **Cadence cost** — tighter crons (backlog #3) increase GitHub Actions usage;
  free for public repos, but flagging the change of pace.
