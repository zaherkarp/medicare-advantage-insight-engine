# Self-Improvement Loop — State

Read this first. Update it in every PR. This file is the loop's durable memory
across sessions: a fresh session resumes by reading [`docs/goal.md`](goal.md)
and this file, then entering the protocol at step (a).

## Status: PAUSED (2026-07-04, after iteration 10 / PR #45)

Owner paused the loop after 11 merged PRs (#35–#45). The self-contained,
clear-cut backlog items are done; what remains is decision-gated (see
**Decision points awaiting owner** below) or needs a feasibility spike (CMS
enrollment). Nothing is in flight — `main` is clean, no open loop PR.

**To resume:** re-enter the protocol at step (a) and pick the top **Backlog**
item — currently *Advisory→config automation* (a small internal item that needs
no owner input). To unblock the higher-value work, an owner decision is needed:
a SEC contact email (lights up SEC filings), a CMS download feasibility spike,
or a yes/no on LLM scoring / a Slack webhook.

Scorecard highlights vs. the Iteration-0 baseline: golden-set P/R 1.00/1.00 over
88 cases (floors 0.95); payer pages 0→31; run time ~105s→~20s; cadence 4h/6h→
2h/3h; near-duplicate suppression on alerts **and** feed; UX checklist 3/4.

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

## Scorecard snapshot (2026-07-04, after iteration 10)

See [`docs/goal.md`](goal.md) for metric definitions, baselines, and targets.

| S1 | S2 (P/R) | S3 floors | C1 | C2 | C3 | F1 | F2 | F3 low-yield | Q1 | Q2 | U1 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 88 | 1.000 / 1.000 | 0.95 / 0.95 | 31/31 | 8/10 | absent | ~20 s (measured locally; confirm in run_metadata post-merge) | 2 h / 3 h | reviewed — verdicts below | 0 / 7 | present (alerts **+ feed**) | 3/4 |

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
| 6 | 2026-07-03 | #41 | Low-yield source review under the new scoring + a blank-item guard in `normalize_items` (a malformed feed had left 30 title-less, summary-less rows in the production archive; such entries are now dropped at normalization with a warning) | F3: reviewed, zero prunes needed — verdicts below | Review verdicts: (1) the 14 "dead" SEC EDGAR feeds are the known 403/User-Agent issue — keep, awaiting the UA decision point; (2) "Managed Healthcare Executive" is a ghost (not in `sources.yaml` or `candidate_sources`); its 30 blank rows are below the display floor and the new guard prevents recurrence; (3) ACHP feed is alive but posts below the 7-day window — keep; (4) MedPAC's RSS is their blog, correctly scoring ~0 — keep; (5) the p2 statewide newsrooms' low yield is by design (MA-context gate). |
| 7 | 2026-07-03 | #42 | Documentation correctness sweep after the 7 feature PRs: README routes table gains `/payers`; five→six categories (README, pr-summary); concurrent-not-sequential fetching + `FETCH_WORKERS` in the `.env` table and architecture.md design row; 2h/3h cadence reconciled (README/operations/assumptions crons); litigation fetcher + `context` source field documented; scoring-factor list in architecture.md expanded (ma_term_boost, exclusions, MA-context gate); project-structure tree updated (`payers.py`, `fetchers/litigation.py`, drop "(stub)") | docs re-synced to shipped state (U1 "accurate docs" restored) | Docs-only; verified by grep sweep for residual "five categor" / "*/4 * * *" / unannotated "sequential". |
| 8 | 2026-07-03 | #43 | Near-duplicate **alert** suppression (no schema change): new `similarity.py` (title-token Jaccard, reusing `keyword_mining._tokens`); `dedupe.suppress_duplicate_alerts` runs between drafting and delivery — within-run it keeps the highest-scoring member of a near-dup cluster, cross-run it drops alerts whose headline matches one delivered in the last `dedup_lookback_days` (via the existing `delivery_log`, no `stories` migration); `storage.recent_alert_titles`; `delivery.dedup` config knobs (enabled/threshold 0.6/lookback 3d); `alerts_suppressed` in the run summary | Q2 absent → **present** | Verified end-to-end across two runs: run 1 drafted 3 → kept 2 (1 within-run dup), run 2's third-outlet repeat → kept 0 (1 cross-run dup); the archive kept every scored item (only the webhook stream is trimmed). 12 new tests; `draft_alerts` gets its first coverage. The `duplicate_of` feed-grouping column is deferred (would need the first `ALTER TABLE` migration idiom) — see backlog. |
| 9 | 2026-07-04 | #44 | Historical trend views: `trends.py` (pure — Monday-bucketing `weekly_series` + `sparkline` geometry), `storage.get_weekly_counts` (12-week series, `entity_aliases`-scoped, buckets on `COALESCE(published_date, fetched_at)`, no schema change), shared `_sparkline.html` partial (inline SVG — line + area wash + end-dot, no JS), wired into `/status` (overall volume) and each payer page (that payer's volume, panel shown only with recent data); theme-aware via CSS vars per the `dataviz` skill | U1 2/4 → **3/4** ("trends" ticks) | Verified end-to-end: built the static site from the production archive and screenshotted `/status` + a payer page over HTTP — sparklines render with valid in-viewBox geometry (monotonic x, y-inverted), area fill, end-dot, and captions ("1394 signals … latest week 224"; payer "36 … latest week 6"). Inline SVG survives the export link-rewriter (only href/src/action are rewritten). 13 new tests. |
| 10 | 2026-07-04 | #45 | Feed near-duplicate grouping — the browsable-archive half of #43's alert dedup. **First `ALTER TABLE` migration**: `_ensure_column` (PRAGMA-guarded, idempotent) adds `stories.duplicate_of`, run from a new `_migrate` in the store constructor. `dedupe.assign_story_duplicates` labels near-dups at persist time (within-run keeps the top-scored representative; cross-run points at the archived root via `recent_story_reps`, never chaining). `_story_filters` gains `include_duplicates=False` → browsable surfaces (feed, topics, states, payers, search, briefing) show one representative; `/status` + `/health` pass `include_duplicates=True` for full counts. Story page shows "Also reported by …" (`get_duplicates`). `processing.story_dedup` knobs, sharing the 0.6 threshold with alert dedup | Q2 present → **present (alerts + feed)** | **Migration verified on the real production DB** (7,970 rows preserved, all NULL, idempotent on reopen). End-to-end: 2 runs, cross-source repeats collapse to one feed row while the archive keeps all; "also reported by" reverse-lookup correct. Forward-looking (existing rows stay representatives). 10 new tests incl. the migration-on-old-schema template. |
| — | 2026-07-18 | #51 | **User-requested** (out of loop order): rework the Post Ideas page (#50) into `/angles` — cards at lens intersections (payer × topic, topic × topic, topic × state, payer × payer) with fact-derived text replacing templated hooks/hashtags, weighted by a declared causal model (`config/causal_model.yaml`: 4 layers, 8 downstream-only edges with citable evidence; `rank_score = count × (1 + boost × edge_weight)`, chain 0.5 / cascade 0.75; soundness validated at load, coverage test-enforced). Uncapped facet query replaces `count_recent_by_category`; `/post-ideas` 301s; static export ships a legacy stub; `seed_test_data.py` now persists stories. | U1 docs re-synced (README, architecture, goal, loop) | Rationale: single-lens `primary_category` grouping made ideas read vague; the multi-category, entity, and state lenses already stored on every story now form the groups, and causally-linked intersections outrank incidental overlaps. 334 tests green incl. 30 engine + 16 model-enforcement tests. |

## Backlog (ordered, next-up first)

| Item | Dimension | Impact | Effort | Status | Notes |
|---|---|---|---|---|---|
| 1. Advisory→config automation | Meta | med | med | next | Mined keywords → *draft* PR (never auto-merged; Guardrail 2). |
| 2. CMS MA enrollment data | Intel depth | high | high | **needs feasibility spike** | Parent-org membership/share on payer pages (C3). Foundational blocker (see decision points): CMS enrollment lives in monthly ZIP-in-CSV files whose URLs 404 and can't be verified via WebFetch; needs net-new binary/CSV handling + a parent-org→slug map. Codebase reuse mapped (storage table, `payers.py`, payer panel), but do a download spike before planning. |

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
- **CMS enrollment data feasibility** (backlog #2): the documented CMS
  "Monthly Enrollment by Contract" URLs 404 and `data.cms.gov` is a JS-rendered
  SPA WebFetch can't read; the parent-org enrollment is a binary ZIP-in-CSV that
  can't be verified without an actual download. Needs a short spike (curl a real
  monthly file, confirm the parent-org column + format) before the feature can
  be planned — and confirmation the file host is reachable from the deploy
  runners.
- **Slack renderer** — needs a Slack workspace/webhook to target.
- **Cadence cost** — the tighter crons shipped in iteration 4 (Pages 2h,
  alerts 3h) increase GitHub Actions usage; free for public repos, but
  flagging the change of pace.
