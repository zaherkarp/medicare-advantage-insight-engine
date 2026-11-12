# Eligibility gate acceptance replay

The production flag stays off. This experiment compares baseline, payer-alias
correction alone, revised eligibility alone, and both together. A source-verified
adjacent event can move from `exclude` to `display`. Payer deals, plan exits, and senior-care transactions with ambiguous scope
appear in the review queue. Evidence does not grant draft or briefing eligibility. Known event
families share one briefing slot in the eligibility arms. Existing archive
duplicate flags still apply. Confirm the appropriate treatment of Part D-only
coverage with the owner before setting an activation criterion for that class.

## October diagnostic and decision

[PR #77](https://github.com/zaherkarp/medicare-advantage-insight-engine/pull/77)
reported 74 feed losses among 177 baseline-visible archive rows. It attributed
68 losses to missing Medicare context and reported zero losses from the alias
correction alone at the feed floor. The original archive export expired.

On the committed October holdout, 179 of 204 rows cleared the display floor.
The original tier excluded 76 of those 179. Applying the evidence file at the
captured scores reduced tier exclusions to 70 and recovered six articles
covering two events, Humana–CenterWell and Aetna–Mercy Care. Those articles are
diagnostic examples for the proposed feed treatment. Fresh-window precision
and recall remain unmeasured.

Keep the production gate off pending a replay of a fresh gate-off archive with
independent labels and a check against the [recall floors](README.md#recall-floors-for-the-october-holdout-pre-registered-2026-10-21).

## Run and review

1. Freeze the candidate and the minimum precision gain before opening fresh
   labels. Obtain a gate-off archive snapshot from the existing export workflow.
   Keep the snapshot private and preserve its hash. Use a fresh time window
   following candidate freeze. End is exclusive.
2. Use one evidence record per article, with an event ID, public URL,
   evidence availability timestamp, status (`confirmed_context` or `review`),
   and a sentence identifying the affected business and consequence. Review
   evidence independently of the acceptance labels. The October YAML is
   diagnostic only. For a historical diagnostic, its timestamps represent when
   the cited information was public, approximated conservatively.
3. Replay and export the blinded packets. Give the reviewer only the blinded
   packet. The separate replay output contains predictions and should stay
   with the evaluator.

```sh
python -m evals.relevance.gate_replay --db /tmp/state.db \
  --since <start-with-offset> --until <exclusive-end-with-offset> \
  --evidence /tmp/evidence.yaml --out /tmp/replay.json \
  --blind-out /tmp/blind.yaml
```

By default the replay uses the archive's saved briefing clocks. Pass repeated
`--clock` arguments only when supplying verified historical run times. Feed is
a snapshot of stories published in the window and fetched before its end.
Drafts are threshold and tier candidates fetched in the window, including
duplicate articles because production drafts are generated before the feed's
duplicate filter. Briefing applies the production score threshold, rolling
window, recency tie-break, and cap. It also requires a story to have been fetched
at run time. Saved article edits, historical duplicate reassignment, and draft
construction errors are outside the replay.

4. Record a reviewer, `blind: true`, `event_id` and a confirmed `label` for every
   row in the review packet. Re-run with `--labels /tmp/labels.yaml`,
   `--prior /tmp/prior.yaml`, and the preregistered `--min-precision-gain`
   expressed as a proportion. The prior manifest contains `event_ids` and
   `items` (title, optional item ID and summary), covering both earlier holdouts,
   the golden set, and the owner development set. If a repeated event is found,
   use a clean window or rebuild the review corpus before scoring. Event IDs
   must be consistent across corpora. An isolated briefing demotion still
   requires editorial review even when the numeric floors pass.

The output contains item-level score changes, tier changes, review decisions,
archive duplicate status, selected IDs, and briefing changes by run. Compare
alias-only and eligibility-only against the baseline, then look at the combined
result for overlap. A confirmed context item below the display score floor can
remain in review and still miss the public feed.
