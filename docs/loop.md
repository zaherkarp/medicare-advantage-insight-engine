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

## Scorecard snapshot (2026-07-03, after iteration 5)

See [`docs/goal.md`](goal.md) for metric definitions, baselines, and targets.

| S1 | S2 (P/R) | S3 floors | C1 | C2 | C3 | F1 | F2 | F3 low-yield | Q1 | Q2 | U1 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 86 | 1.000 / 1.000 | 0.95 / 0.95 | 31/31 | 8/10 | absent | ~20 s (measured locally; confirm in run_metadata post-merge) | 2 h / 3 h | 41 of 63 | 0 / 8 | absent | 2/4 |

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
| 4 | 2026-07-03 | #38 | Parallel source fetching (ThreadPoolExecutor in `main._fetch_all_sources`, `FETCH_WORKERS` env, default 8, 1 = sequential; config-order results, per-source error isolation preserved) + cadence tightened: Pages 4h → 2h, alerts 6h → 3h | F1 47–105 s → ~20 s measured on a live fetch of all 91 sources; F2 4h/6h → 2h/3h | Live run confirmed the SEC root cause: **every SEC EDGAR feed returns 403 Forbidden** under the default User-Agent (no contact info). Fix moved to a decision point — needs an owner-chosen contact email for the UA. |
| 5 | 2026-07-03 | #39 | Scoring gap fixes, informed by the iteration-2 labeled data: removed noise keywords (ACA-marketplace set, bare `bid`/`regulation`/`regulatory`/`provider`/`commission`), added M&A vocabulary (`divestiture`/`divest`/`sale`/`sell`), oversight/legal vocabulary (`OIG`/`overpayment`/`upcoding`/`fraud`/`settlement`), `8-K`, network-dispute phrases, `broker/agent commission`; 7 soft exclusions (FFS payment machinery, Medicaid work requirements, rural-health grants, abortion/mifepristone); new **`ma_boost_terms`** scoring mechanism (+0.15 once for core MA vocabulary — "Medicare Advantage", "D-SNP", …); Optum added as watched entity + UnitedHealthcare alias; golden set 83 → 86 with new guards; CI floors 0.9 → 0.95 | S2 0.914/0.914 → **1.000/1.000** (all six documented gaps fixed); S3 0.9 → 0.95; Q1 0/0 → 0/7 | Whole-archive rescore of 7,970 production stories: alert-grade 462 → 275 (+70 genuine signals up incl. GoHealth Chapter 11 at 0.06→0.48, −257 noise down), public-grade 1,490 → 595. Spot-checked every dropped Medicare-titled story — all FFS/PSA/off-domain. |
| — | 2026-07-03 | #40 | **User-requested** (out of loop order): integrate Georgetown Law's Health Care Litigation Tracker. Generic `SourceConfig.context` field + new `litigation` fetcher (`fetchers/litigation.py`) that injects the source's guaranteed topic into each case's boilerplate summary; 4 MA-subtopic feeds added (star ratings, coding practices, coverage denials, enrollment practices) at priority 4; golden set 86 → 88 with two litigation guards | S1 86 → 88; new source class | Case summaries are boilerplate, so without context injection named-payer cases scored 0.28 (sub-alert) and unwatched-plaintiff cases 0.08 (hidden). With injection: named-payer 0.61, unwatched 0.41. Live end-to-end: all 34 cases ingest, 31 alert-grade, landing on 9 payer pages (Elevance Star Ratings suit → `/payers/elevance`). |

## Backlog (ordered, next-up first)

| Item | Dimension | Impact | Effort | Status | Notes |
|---|---|---|---|---|---|
| 1. Low-yield source review | Coverage | med | low | next | 41 flagged sources incl. "Managed Healthcare Executive" (30 items, 0 public, max 0.06). SEC-feed 403 fix is a decision point (owner contact email for the UA), tracked below. |
| 2. Near-duplicate alert suppression | Signal quality / UX | med | med | queued | Title-similarity clustering at draft time; `duplicate_of` column via guarded migration (Guardrail 3). Answers `docs/assumptions.md` open questions. |
| 3. Historical trend views | Intel depth / UX | med | med | queued | Signal volume by payer/category/week; inline SVG sparklines (static-export safe) on `/status` + payer pages. |
| 4. CMS MA enrollment data | Intel depth | high | high | queued | Monthly CPSC files → parent-org membership/share on payer pages; likely two PRs (fetch/store, then UI). |
| 5. Advisory→config automation | Meta | med | med | queued | Mined keywords → *draft* PR (never auto-merged; Guardrail 2). |

## Blocked / parked

*(none yet)*

## Decision points awaiting owner

- **SEC feeds return 403** (confirmed by live fetch, iteration 4): SEC EDGAR
  requires a User-Agent with contact information, and the default UA has none —
  this is why the production archive has zero SEC stories. Fix = set
  `USER_AGENT` (e.g. `MA-Signal-Monitor/1.0 (contact: you@example.com)`) as a
  repo secret/Variable used by both workflows. Needs an owner-chosen email
  that's acceptable to send to SEC with every request.
- **Semantic/LLM scoring** — would add a paid dependency to a deliberately
  free/local app (Guardrail 4). If approved: LLM as advisory re-ranker first,
  gated by the golden set.
- **Slack renderer** — needs a Slack workspace/webhook to target.
- **Cadence cost** — the tighter crons shipped in iteration 4 (Pages 2h,
  alerts 3h) increase GitHub Actions usage; free for public repos, but
  flagging the change of pace.
