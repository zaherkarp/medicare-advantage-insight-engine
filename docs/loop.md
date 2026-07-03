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

## Scorecard snapshot (2026-07-03)

See [`docs/goal.md`](goal.md) for metric definitions, baselines, and targets.

| S1 | S2 (P/R) | S3 floors | C1 | C2 | C3 | F1 | F2 | F3 low-yield | Q1 | Q2 | U1 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 20 | 1.00 / 1.00 | 0.80 / 0.80 | 0/31 | 8/10 | absent | 47–105 s | 4 h / 6 h | 41 of 63 | 0 / 0 | absent | 1/4 |

## Iteration log

| # | Date | PR | Shipped | Scorecard delta | Notes |
|---|---|---|---|---|---|
| 0 | 2026-07-03 | — | Baseline measurement (tests, golden set, published archive, run durations) | established baselines | Feedback table is empty → exclusion mining (backlog #4) must use archive score distributions, not owner verdicts. |
| 1 | 2026-07-03 | (this PR) | Docs truth pass (SEC/CMS fetchers live, 6 categories, 8 tables, historical banners on pr-summary/qa-results) + `docs/goal.md` + `docs/loop.md` | U1 0/4 → 1/4 | Proves the PR → CI → merge → restart loop end to end. |

## Backlog (ordered, next-up first)

| Item | Dimension | Impact | Effort | Status | Notes |
|---|---|---|---|---|---|
| 1. Golden set 20 → 80+ and `scripts/scorecard.py` | Signal quality | high | med | next | Hard negatives (Medicaid-only, non-payer earnings, "premium"/"network" traps for the MA-context gate); mine candidates from published archive; raise floors to 0.9/0.9 if margin allows. Must precede exclusions + dedup work. |
| 2. Per-payer pages `/payers` + `/payers/{slug}` | Intel depth | high | med | queued | `stories.entities` already persisted as JSON; mirror the `/states` pattern; canonical alias map (UHC/UnitedHealth → one page); signals, category mix, SEC filings, state footprint; add to static-export crawl. |
| 3. Parallel source fetching + cadence bump | Freshness | high | low | queued | ThreadPoolExecutor in `main._fetch_all_sources`, `fetch_workers` escape hatch, per-source error isolation preserved; then crons 4h→2h (Pages), 6h→3h (alerts). |
| 4. Populate `exclusions.hard`/`.soft` | Signal quality | med | med | queued | Feedback table empty → mine from archive score distributions + low-yield source review instead of owner verdicts; every exclusion guarded by a golden-set entry. |
| 5. Near-duplicate alert suppression | Signal quality / UX | med | med | queued | Title-similarity clustering at draft time; `duplicate_of` column via guarded migration (Guardrail 3). Answers `docs/assumptions.md` open questions. |
| 6. Historical trend views | Intel depth / UX | med | med | queued | Signal volume by payer/category/week; inline SVG sparklines (static-export safe) on `/status` + payer pages. |
| 7. CMS MA enrollment data | Intel depth | high | high | queued | Monthly CPSC files → parent-org membership/share on payer pages; likely two PRs (fetch/store, then UI). |
| 8. Low-yield source review | Coverage | med | low | queued | 41 flagged sources incl. "Managed Healthcare Executive" (30 items, 0 public, max 0.06 — likely title-only feed or gate issue; investigate before pruning). |
| 9. Advisory→config automation | Meta | med | med | queued | Mined keywords → *draft* PR (never auto-merged; Guardrail 2). Only after items 1 and 4 prove out. |

## Blocked / parked

*(none yet)*

## Decision points awaiting owner

- **Semantic/LLM scoring** — would add a paid dependency to a deliberately
  free/local app (Guardrail 4). If approved: LLM as advisory re-ranker first,
  gated by the golden set.
- **Slack renderer** — needs a Slack workspace/webhook to target.
- **Cadence cost** — tighter crons (backlog #3) increase GitHub Actions usage;
  free for public repos, but flagging the change of pace.
