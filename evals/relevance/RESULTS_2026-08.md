# Held-out evaluation results — 2026-08

Scored **once** on the owner-labeled held-out set (`holdout_2026-08.yaml`, n=122,
drawn from the post-2026-08-04 window the taxonomy was never tuned on). 98 labels
are owner-reviewed; 24 are a my-fill remainder (feed/alert placement,
`label_source: fill`) that does not affect the brief-worthy or noise calls below.

The owner's briefing bar is strict: of 122 stories, only **4** are "brief-worthy"
and **8** more are alert-worthy context. That scarcity is why the precision
numbers look low in absolute terms — read them as *current vs candidate*, not
against 100%.

## Headline: what reaches the surfaced stream

| | Current (score ≥ 0.3) | Candidate — brief tier | Candidate — alert tier |
|---|---|---|---|
| Items surfaced | 58 | 16 | 20 |
| **Noise rate** (owner `irrelevant`) | **62%** (36/58) | **25%** (4/16) | **20%** (4/20) |
| Brief-worthy density | 5% | 25% | 20% |
| Not-noise (MA-relevant + display) | 38% | 75% | 80% |

**The candidate cuts noise in the surfaced stream from 62% to ~20–25%** — a ~3×
precision gain on blind data — by eliminating the gross false-positive classes
(Medicaid-only, criminal, commercial/ACA, general-CMS) the Aug-14 briefing showed.

## Must-catch recall — no loss, a small gain

| | Current | Candidate |
|---|---|---|
| Brief-worthy signals surfaced | 3/4 (75%) | **4/4 (100%)** |
| Alert-worthy context surfaced | — | 4/8 (50%) |

The candidate briefs **all 4** brief-worthy signals — one better than current,
which hides the 0.21-scored "Providence winds down its MA plan" below its own
threshold. Being eligibility-gated rather than score-gated is why.

## Two honest limitations this surfaced

1. **The gate is not the owner's editorial bar.** It briefs 16; only 4 are
   truly brief-worthy, 8 are display-worthy, 4 are noise. The fine
   "briefing-worthy vs merely MA-relevant" call is beyond a keyword gate — this
   is exactly where a human or a borderline adjudicator sits, and the residual
   is now quantified (12 of 16).
2. **Text-only blind spot (found during owner gap-review).** The gate misses 4
   MA-relevant context signals whose MA-ness lives in the *source/metadata*, not
   the snippet text — notably `United States v. Anthem` (an MA RADV / False
   Claims Act filing) and `Clover posts $28M Q2 profit` (a pure-MA insurer,
   phrased without MA vocabulary). The current keyword scorer misses these too.
   The fix is **source-tier trust** for the curated FCA/MA litigation feeds, not
   the gate.

## Recommendation

The deterministic gate **clears the acceptance bar as a precision filter**:
material precision gain on a blind holdout (62% → ~22% noise), zero loss of
must-catch brief-worthy signals (75% → 100%), false positives categorized. It
does **not** replace editorial curation of the briefing, and it inherits a
text-only recall blind spot on source-curated litigation.

→ **Proceed to wire it in behind a default-off flag** (follow-up PR), positioned
as an **eligibility/precision filter on the alert + briefing stream**, with two
documented next steps: (a) a source-tier rule so the FCA/MA litigation feeds are
trusted, and (b) a borderline adjudicator for the brief-vs-display editorial call
— both evaluated the same way, on the next held-out window.

## Caveats

- n=122 with only 4 brief-worthy labels — precision/recall CIs are wide; this is
  a directional read, not a tight estimate. Repeat on the next window.
- 24/122 labels are my-fill (feed/alert placement); every brief-worthy and noise
  call in the headline is owner-reviewed.
- Single 10-day window; no cross-policy-cycle stability test yet.
