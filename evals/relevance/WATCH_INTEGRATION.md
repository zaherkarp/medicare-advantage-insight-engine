# NEWS-WATCH-03 current-main integration

This executable development replay reconnects the retained October Watch packet
to the current web-only engine. The replay itself does not change production
scoring, taxonomy, eligibility gates, outbound delivery, or owner-labelled holdouts.

Baseline: production main `fb58e875fbe610f378f145e970f171820d0ee8d5`.
Retained proposal: `854b43c0253f8ab0bc64f9370c7c2c7f16fef90d`.
The 13-case YAML originally matched that proposal byte-for-byte. Its labels are
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

The offline KFF proposal moves 0.21 to 0.31 and creates a saved draft; that research
boost remains unpromoted. The isolated scorer snapshot cannot be imported
by production paths. Future scorer changes must reconcile it with then-current
main, not copy it wholesale. Config changes require the repository's owner-label
or reviewed-evidence gate, and new golden-set data belongs in a separate PR.

This is not live acquisition, recall/precision validation, or a completed
Watch-to-LinkedIn loop. There is still no per-claim support guard. Drafts are
review-only summary templates, not approved posts. A fresh unseen acceptance
window remains separate from these development cases. PR74 remains separate;
this branch does not inherit its replay or revive its parked work.

## Contextual SCAN repair

The production matcher now separates SCAN identity from ordinary scan/scans,
requires local organization context, and rejects imaging and sentence-boundary
false matches. The ordinary-scan case's current observation changes from score
1.0 with a draft and SCAN entity to 0.06 with neither. Its skipped recommendation,
input, provenance and all retained-candidate expectations are unchanged. The
other twelve current-case expectations are unchanged. Regenerated results report
the actual current scorer hash; the historical observation is retained in the
fixture comment and repository history.

Focused tests assert the approved development negative's entity exclusion and
exercise saved facets/drafts through the real persistence and Angles paths.
Golden-set coverage is supplied separately in PR82, as required by AGENTS.md;
the behavior repair must not ship without that coverage. No gate or research
boost is enabled. Existing stored rows are not automatically rescored, so this
does not establish that a previously published false chain has disappeared.
The existing backfill does not consistently rebuild all derived fields; a
separate targeted archive-repair contract and authorized live verification are
still required. A global eligibility flip would also lose standalone Part D
coverage and is outside this repair.
