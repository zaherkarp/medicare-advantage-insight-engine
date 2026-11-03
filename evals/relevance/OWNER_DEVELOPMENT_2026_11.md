# Three approved development examples

The owner approved these three behavior examples on November 2, 2026. The
public-safe record is `tests/fixtures/owner_development_2026_11.yaml`. Source
URLs were re-opened from their public publishers before preparing this data.
Inputs are short paraphrases, not exports of private archives or conversations.
Stable IDs identify source cases; they do not claim exact captured input bytes.

The data keeps relevance, newsworthiness, briefing/draft eligibility and entity
exclusions separate. In particular, the Medicare Plan Finder FAQ is relevant
reference material, not a binary irrelevant news example. Empty entity-exclusion
lists mean no additional approved entity judgment, not exhaustive correctness.

## Acceptance and limits

- Exactly three approved development labels; no provisional examples mixed in.
- Preserve the conditional FAQ judgment and potential-denial caveat for KFF.
- No production scorer/configuration, existing golden set/floors, or holdout edits.
- Load all three through actual scoring, drafting and persistence without
  changing labels to fit output. Tests verify data shape and path execution,
  **not** that current output satisfies the desired labels.
- The persistence smoke test uses a fixed synthetic November 2 clock; it does
  not imply that these October articles were newly published on that date.
- Full tests/lint and distinct exact-commit review before readiness. Owner
  approval to merge this data PR is separate from production tuning/release.

This is a separate golden-data preparation step, not a newly enforced selection
gate. The existing binary golden scorer conflates score threshold with drafting
and cannot express reference-only relevance. Future selection validation must
compare each appropriate output axis and preserve failures honestly. Do not
turn the 13 earlier assistant-recommended fixtures into owner labels, and do
not reuse any development example as an unseen acceptance sample.
