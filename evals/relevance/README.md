# Relevance precision experiment

**Goal:** make the engine distinguish a decision-relevant *Medicare Advantage*
signal from healthcare news that merely brushes CMS, Medicare, Medicaid, or a
payer name — measured honestly, on data the taxonomy was never tuned on.

This directory is a **research subsystem**. Nothing here is imported by the
shipped ingestion / scoring / briefing / delivery path, so the production
pipeline and the golden-set gate are unchanged. It is the reversible first
phase of the experiment: build the held-out label set and the harness; the
production wiring lands in a follow-up PR only if a held-out evaluation clears
the acceptance bar below.

## Why this exists

The motivating failure: the **2026-08-14 Daily Briefing** carried six items and
only one (*"Aetna's value-based care success in Medicare Advantage"*) was a
genuine MA signal. The other five were, verbatim from the archive with their
stored `scoring_breakdown`:

| Item | Score | Why it scored |
|---|---|---|
| Arkansas "private option" Medicaid | 0.33 | `"CMS"` in title (0.27) — the only content signal |
| CMS gender-affirming-care rule | 0.33 | `"CMS"` in title (0.27) — general rulemaking |
| Mangione guilty plea (UHC CEO) | 0.46 | +0.40 from `UnitedHealthcare`+`UnitedHealth` counted as two entities; no keyword |
| UnitedHealth $237M insider suit | 0.66 | `"sale"` + the same entity double-count |
| Hippocratic AI voice agents | 0.54 | `"platform"`+`"star rating"` — an AI vendor claiming Stars applicability |

**Root cause:** there is no MA-eligibility gate. The existing "MA-context gate"
is satisfied by the bare word `Medicare`/`CMS` or any watched-payer name, so
Medicaid rules, general CMS rules, and a payer CEO's criminal case all clear it
(the config author documents this in `config/taxonomy.yaml` lines 325–334).

## The candidate: a two-tier eligibility gate (`eligibility.py`)

A tier gate, deliberately separate from the additive score, expressing the owner
rubric decisions (2026-08-15) directly:

```
BRIEF   > ALERT   > DISPLAY  > EXCLUDE
```

- **BRIEF** — carries MA-specific vocabulary (Medicare Advantage, Part C, D-SNP,
  star ratings, risk adjustment, …). A real MA signal → Daily Briefing.
- **ALERT** — a watched payer + Medicare context but no MA-specific term
  ("defensible MA implication"). Push-alert stream, but not the stricter briefing.
- **DISPLAY** — Medicare-adjacent only (bare Medicare/CMS/Part D), no MA line of
  business → kept in the archive/feed, kept **out** of briefing + alerts
  ("display-only, not briefed").
- **EXCLUDE** — an owner-designated noise class (Medicaid-only, general-CMS
  non-MA rulemaking, payer criminal/reputational, payer commercial/ACA), unless
  rescued by an MA-specific term.

Plus one mechanical score fix, `entity_group_delta`: collapse payer aliases to
distinct groups so `UnitedHealthcare`+`UnitedHealth` is +0.20, not +0.40.

> Owner decision on record: **AI-vendor "Stars" product claims are NOT an
> exclusion class.** The Hippocratic-AI item stays eligible; catching it is
> deferred to a later borderline adjudicator, not this deterministic gate.

## Files

| File | What it is |
|---|---|
| `eligibility.py` | The candidate two-tier gate + entity-group dedup. Pure, tested, unwired. |
| `build_holdout.py` | Deterministic builder of the held-out set from a published `state.db`. |
| `holdout_2026-08.yaml` | The held-out set (n=122). **`proposed_label` is a heuristic; `label` is empty pending owner review.** |
| `scorecard.py` | Labels-vs-archive precision/recall + FP-type breakdown, current vs candidate. |
| `../../tests/test_relevance_eligibility.py` | Regression tests pinning the tier decisions on the Aug-14 cases. |

## Run it

```bash
# 1. get the same archive deploy-pages.yml publishes
curl -fsSL https://zaherkarp.github.io/medicare-advantage-insight-engine/data/state.db -o /tmp/state.db

# 2. (re)build the held-out set — deterministic, no RNG
python -m evals.relevance.build_holdout --db /tmp/state.db --out evals/relevance/holdout_2026-08.yaml

# 3. OWNER STEP: fill `label` for each item (confirm/correct proposed_label)

# 4. score current selection vs the candidate against the labels
python -m evals.relevance.scorecard --holdout evals/relevance/holdout_2026-08.yaml
```

## Evaluation discipline (non-negotiable)

- **Dev vs holdout.** `tests/fixtures/golden_set.yaml` is the *development* set —
  the taxonomy was tuned until it scored 1.00/1.00 there, so that number is
  resubstitution, not performance. This held-out set is drawn from the
  **post-2026-08-04** window (after the last taxonomy change) and is scored
  **once**. Rules are tuned on dev, never on the holdout.
- **Labels are owner ground truth.** `proposed_label` exists only to make review
  a confirm/correct pass. Until `label` is filled, `scorecard.py` runs but its
  candidate column is **circular** (the proposed labels come from the candidate)
  and prints a loud provisional warning. No precision number is reported from
  proposed labels.
- **Anti-contamination.** The held-out set is content-hashed (`_meta.content_sha256`)
  and excludes titles already present in `golden_set.yaml`.

## Acceptance / rollback / stop (for the follow-up production PR)

Wire the gate into `scoring.py`/`digest.py` behind a **default-off** flag only if,
on the owner-labeled holdout:

- **Accept** — briefing precision materially up vs current; must-catch recall
  (labeled `relevant_brief` reaching the briefing bar) at or above the agreed
  floor; every briefed item has a defensible MA rationale; this fixture committed.
- **Roll back** — holdout recall breaches the floor, or a must-catch class is
  silently lost. Revert the flag (deterministic, clean).
- **Stop / escalate** — precision gains are real but the residual commercial /
  vendor-Stars band stays above tolerance: that is the signal a borderline LLM
  adjudicator has earned its place, evaluated the same way.

## Status

- [x] Held-out set built (n=122: 58 alert-grade, 39 display-band, 25 sub-floor).
- [x] Candidate gate + harness + regression tests committed; full suite green;
      production untouched.
- [x] Owner labeling of `holdout_2026-08.yaml` (98 owner-reviewed, 24 my-fill).
- [x] Scored once — see [`RESULTS_2026-08.md`](RESULTS_2026-08.md). Noise in the
      surfaced stream 62% → ~22%; must-catch brief recall 75% → 100%. Clears the
      precision-filter bar; does not replace editorial curation; text-only recall
      blind spot on source-curated litigation surfaced.
- [ ] **Follow-up PR: wire the gate behind a default-off flag** ← next, as an
      eligibility/precision filter, with a source-tier rule for the FCA/MA feeds
      and a borderline adjudicator for the brief-vs-display call as documented
      next steps.
