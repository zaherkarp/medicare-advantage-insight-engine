# Held-out evaluation results, 2026-10

Scored **once** on `holdout_2026-10.yaml`: stories fetched 1-15 October 2026,
after the gate vocabulary was last changed (2026-08-17), with no August holdout
item in it. All 204 labels are the owner's, entered 2026-11-03 on a labeling
page that hid the gate's guess.

**Four stories are excluded from scoring** (n=200), because they were reused as
development material after the holdout was built: three in
`tests/fixtures/owner_development_2026_11.yaml` (one of which drove the SCAN
identity fix in `scoring.py`) and one in `watch_selection_cases.yaml`. They are
listed in the file's `_meta.excluded_from_scoring`. Including them changes no
conclusion (`--include-excluded`: recall 54% vs 53%).

## Verdict: the gate fails all three recall floors. Do not switch it on.

The floors were set by the owner on 2026-10-21, before any October label was
scored (see README):

| Floor | Result |
|---|---|
| No `relevant_brief` story hidden from the feed | **FAIL**: 15 hidden |
| At most one `relevant_brief` story demoted below the briefing | **FAIL**: 12 demoted |
| No `relevant_context` story hidden from the feed | **FAIL**: 13 hidden |

## Headline numbers

| | Current (score >= 0.3) | Gate, brief tier | Gate, alert tier |
|---|---|---|---|
| Stories surfaced | 118 | 54 | 55 |
| Briefing precision (labeled Brief) | 40% (47/118) | 57% (31/54) | |
| Recall of Brief stories | **81%** (47/58) | **53%** (31/58) | |
| Precision, Brief or Context | 55% (65/118) | | 71% (39/55) |

The gate still raises precision, as it did in August. It now pays for it with
recall: it loses 27 of the 58 stories you would brief, against 11 lost by the
current threshold.

## Why: the bar the gate encodes is not the bar in these labels

The gate requires Medicare Advantage vocabulary to brief a story and Medicare
context to keep it on the feed. These labels brief and keep a wider set:

- **Payer business news without Medicare words.** Hidden but labeled Brief:
  Humana and BlackRock's $2.8B CenterWell deal (three versions), Centene's gold
  card and CHOICE expansion, the Legacy-UnitedHealthcare contract, SCAN's 2027
  slate with a Costco plan, Clover's data breach. Hidden but labeled Context:
  Cigna's modernization and Health Sense, Ascension leaving insurance, Texas
  investigating UnitedHealth. All name a watched payer; none clear the gate.
- **Medicaid and ACA payer news.** Labeled Brief: Molina's $6B Florida
  Medicaid contract, UnitedHealthcare exiting Georgia's ACA marketplace. The
  gate excludes these classes by design (owner rubric, 2026-08-15).
- **CMS rulemaking and Part D.** Demoted to display but labeled Brief: price
  transparency, most-favored-nation pricing, CMS exclusion authority, two Part D
  stories. The gate treats bare Medicare/CMS language as display-only.

The labels also differ in scale from August: 58 of 200 Brief here, 4 of 122
there. The first week of October carries the 2027 plan landscape and the start
of open enrollment, so some of that is real news volume. But several Brief
labels sit in classes the August rubric excluded (Medicaid-only, payer
commercial/ACA), so the bar itself has moved. Which bar is intended decides
what to build next; the gate cannot meet this one by tuning its vocabulary.

## What the current threshold misses

The score threshold loses 11 Brief stories, all from the band sampled just
below it (score 0.1 to 0.3). None of the 25 sampled stories below 0.1 was
labeled Brief or Context. This sample does not establish that the display
floor is safe for the broader population; that remains unverified.

## Caveats

- Recall is measured on a stratified sample: every story over the threshold,
  but only 57 of the band below it and 25 below the floor. It is not a
  population rate.
- The owner agreed with the gate-derived `proposed_label` on 88 of 200 (44%),
  which the labeling page never showed.
- One 15-day window, during plan-year announcements and the start of open
  enrollment.
- The 1-15 October shadow report covered this same window, so these titles have
  been seen. A rule written in response to these failures must be judged on a
  later window.
