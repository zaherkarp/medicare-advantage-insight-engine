# Goal: The Best Competitive News Tracker in the Medicare Advantage Space

## Mission

MA Signal Monitor is the best **free, self-hosted competitive news tracker for
the Medicare Advantage market**: an analyst opening the site should learn what
changed in MA — per payer, per state, per topic — faster and with less noise
than any other free source.

This document defines what "best" means, how it is measured, and the guardrails
that every change must respect. The improvement loop that works toward this
goal lives in [`docs/loop.md`](loop.md).

## What "best" means

| Dimension | Definition |
|---|---|
| **Signal quality** | High precision *and* recall against a growing hand-labelled benchmark; noise never reaches the public feed; every scoring change is regression-gated. |
| **Competitive intel depth** | Payer-level answers: what is Humana/UHC/Centene doing, what did they file, how is their enrollment trending — not just a chronological feed. |
| **Coverage & freshness** | Broad sources (national + all 50 states + SEC + CMS), fast pipeline, ingest cadence limited by cron rather than run time, low-yield sources reviewed and pruned. |
| **Product & delivery UX** | Feed, topics, states, payers, trends, briefing, angles, and search all work on the static Pages site; alerts are deduplicated and suppressible; docs stay accurate. |

## Scorecard

Baselines measured 2026-07-03 against the live published archive
(7,970 stories; 1,490 public-grade ≥ 0.1; 462 alert-grade ≥ 0.3).
The current snapshot is maintained in [`docs/loop.md`](loop.md).

| # | Metric | How measured | Baseline (2026-07-03) | Target |
|---|---|---|---|---|
| S1 | Golden-set size | entries in `tests/fixtures/golden_set.yaml` | 20 (10 relevant / 10 irrelevant, all easy cases) | ≥ 80 incl. hard negatives |
| S2 | Golden-set precision / recall | `scripts/scorecard.py` | 1.00 / 1.00 (margins: hardest relevant 0.33, hardest negative 0.195 vs 0.3 threshold) | ≥ 0.90 / 0.90 on the expanded set |
| S3 | CI floor values | `tests/test_golden_set.py` | 0.80 / 0.80 | 0.90 / 0.90 |
| C1 | Watched payers with a dedicated intelligence page | web routes | 0 of 31 | all watched entities |
| C2 | Top-10 national payers with ≥ 3 signals in last 30 days | archive query on `stories.entities` | 8/10 (Kaiser and Molina at 1 each) | 10/10 |
| C3 | Enrollment/market-share data on payer pages | feature | absent | present (CMS monthly enrollment files) |
| F1 | Pipeline run wall time | `run_metadata` (run_start → run_end) | 47–105 s (sequential over 91 sources) | < 60 s |
| F2 | Ingest cadence (Pages / alerts) | workflow crons | 4 h / 6 h | 2 h / 3 h once F1 is met |
| F3 | Low-yield sources (≥ 25 items, < 5 % alert-grade) | source-yield query | 41 of 63 high-volume sources | reviewed: pruned, downgraded, or justified |
| Q1 | `exclusions.hard` / `.soft` populated | `config/taxonomy.yaml` | 0 / 0 | mined + hand-reviewed, each guarded by a golden-set entry |
| Q2 | Near-duplicate alert suppression | feature | absent | present |
| U1 | UX checklist: payer pages, trend views, story cross-links, accurate docs | manual | 1/4 (docs accurate as of this commit) | 4/4 |

## Guardrails

Binding on every iteration, no exceptions:

1. **The golden-set test passes on every PR.** Floors may only ever be raised,
   never lowered. No scoring/taxonomy change ships without golden-set coverage
   of the behavior it changes (expand the set if needed).
2. **Crowd feedback stays advisory forever.** Only owner feedback and
   hand-reviewed mined keywords may drive config changes, and always via a
   reviewable PR diff.
3. **Schema changes must migrate the existing `state.db` in place.** The
   published Pages DB is carried forward by `deploy-pages.yml`; a breaking
   schema change silently destroys the production archive. Follow the guarded
   `ALTER TABLE` migration pattern in `storage.py`.
4. **No paid dependencies** (LLM APIs included) without an explicit owner
   decision — the app is deliberately free and local.
   **Amended 2026-08-08 (owner decision): scoped exemption for research.** Paid
   embedding/LLM APIs are permitted inside `src/ma_signal_monitor/research/` and
   the `[research]` install extra, for the retrieval-research workstream
   ([`docs/research/00-repository-assessment.md`](research/00-repository-assessment.md)).
   The guardrail remains **fully binding on the application**: ingestion,
   scoring, classification, drafting, delivery, the web app, and the deploy
   pipeline stay free and local, and `pip install .` must never pull a paid
   dependency. CI holds no API keys — every LLM-touching test is fixture-mocked
   so `pytest` stays offline and deterministic.
5. **Every change lands via PR with green CI**; one logical change per PR.
6. **`ruff format`, `ruff check`, and the full `pytest` suite pass locally
   before every push.**
7. **Research code never writes to `state.db`.** The retrieval-research
   subsystem reads the archive through `StateStore(..., read_only=True)` and
   keeps its own corpus in a separate database. Guardrail 3 makes any schema
   change to `stories` a production risk; the research layer must not take one.
