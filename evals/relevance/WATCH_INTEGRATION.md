# NEWS-WATCH-03 current-main integration

This executable development replay reconnects the retained October Watch packet
to the current web-only engine. It does **not** change production scoring,
taxonomy, eligibility gates, outbound delivery, or owner-labelled holdouts.

Baseline: production main `fb58e875fbe610f378f145e970f171820d0ee8d5`.
Retained proposal: `854b43c0253f8ab0bc64f9370c7c2c7f16fef90d`.
The 13-case YAML is preserved byte-for-byte from that proposal. Its labels are
`assistant_recommendation_not_owner_confirmed`, not owner truth. Three minimized
SCAN/FAQ controls are not copies of the unavailable original comparison bundle.

## Fixed acceptance contract

- Run the actual current scorer as the baseline, without monkeypatching it.
- Compare the isolated retained scorer with the gate off; report hypothetical
  gate-on results separately. Neither candidate is shipped behavior.
- Exercise current `_persist_stories`, duplicate assignment, decoded Angles
  facets, and saved `public_draft` readback. Never restore retired alert delivery.
- All 13 frozen expected outcomes must match; KFF baseline/candidate distinction,
  SCAN organization positives/verb negative, FAQ exclusion and duplicate grouping
  remain explicit. A missing persistence path must fail the test.
- Keep provenance, source hashes, fixed October clock and selection overrides in
  the result. Run repeatedly and obtain identical output.
- Pass repository lint/full tests and separate exact-commit review. Stop at a
  reviewable PR; merge and deployment remain owner decisions.

Run `python -m evals.relevance.watch_selection --output
 evals/relevance/watch_selection_results.json` (on one command line), then
`python -m pytest tests/test_watch_selection.py`.

## What remains

The offline KFF proposal moves 0.21 to 0.31 and creates a saved draft; production
remains unchanged. The isolated scorer snapshot intentionally cannot be imported
by production paths. Future scorer changes must reconcile it with then-current
main, not copy it wholesale. Config changes require the repository's owner-label
or reviewed-evidence gate, and new golden-set data belongs in a separate PR.

This is not live acquisition, recall/precision validation, or a completed
Watch-to-LinkedIn loop. There is still no per-claim support guard. Drafts are
review-only summary templates, not approved posts. A fresh unseen acceptance
window remains separate from these development cases. PR74 remains separate;
this branch does not inherit its replay or revive its parked work.
