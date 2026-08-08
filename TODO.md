# TODO

An actionable snapshot of open work. The **curated backlog, iteration log, and
full context live in [`docs/loop.md`](docs/loop.md)** (the self-improvement
loop's durable memory); the strategic targets and scorecard are in
[`docs/goal.md`](docs/goal.md). The loop is currently **paused**, so this file is
the human-readable to-do surface — keep the two in sync when you pick something
up.

## Ready to pick up (no owner decision needed)

- [ ] **Advisory → config automation.** Turn mined keyword suggestions into a
  *draft* PR (never auto-merged — the owner reviews the diff). Loop backlog #1.
- [ ] **Populate `exclusions.hard`.** Soft exclusions exist (7); the hard-veto
  list is still empty. Mine candidates, hand-review, and guard each with a
  golden-set entry so it can't silently regress. (goal.md Q1)

## Noise-reduction follow-ups (from the display-floor + MA-context-gate work)

- [ ] **Optional: opt specific states into health-scoped feeds.** A validated
  `/category/health/feed/` mapping exists for ~21 statewide newsrooms. Deferred
  on purpose: it trades recall (drops cross-category Medicaid/Medicare stories
  filed outside "health") for lower ingest volume, and the MA-context gate
  already neutralizes the political noise. Apply per-state only if ingest volume
  becomes a concern.
- [ ] **Keep an eye on the gate's recall** as sources change — genuine
  state-level MA stories should still surface (they name Medicare or a payer). If
  something legitimate is being suppressed, add the missing anchor to
  `ma_context_terms`, add the payer to `watched_entities`, or lower
  `scoring.ma_context_min_priority`.

## Needs an owner decision (blocked)

- [ ] **SEC EDGAR feeds return 403.** SEC requires a `User-Agent` with contact
  info; the default has none, so the archive has **zero** SEC filings. Set
  `USER_AGENT` (e.g. `MA-Signal-Monitor/1.0 (contact: you@example.com)`) as a
  repo secret/Variable used by both workflows. Needs an owner-chosen email.
- [ ] **CMS MA enrollment data.** Parent-org membership/market-share on payer
  pages (goal.md C3, would take U1 to 4/4). Blocked on a download feasibility
  spike — the monthly files are ZIP-in-CSV and the documented URLs 404. Confirm a
  reachable file + parent-org column before planning.
- [x] **Semantic / LLM scoring.** ~~Would add a paid dependency to a deliberately
  free/local app (Guardrail 4).~~ **Decided 2026-08-08:** Guardrail 4 amended
  with a *research-only* exemption — paid embedding/LLM APIs are allowed inside
  `src/ma_signal_monitor/research/` and the `[research]` extra, and remain
  prohibited in the application itself. Semantic retrieval is therefore pursued
  as a measured research condition (evaluated against BM25 and the existing
  transparent scorer), **not** as a change to production scoring. Any later
  proposal to move a semantic signal into the app is a separate owner decision
  and would still start as an advisory re-ranker gated by the golden set. See
  [`docs/research/00-repository-assessment.md`](docs/research/00-repository-assessment.md).
- [ ] **Slack renderer.** Needs a target Slack workspace/webhook.
- [ ] **Cron cadence cost.** Pages 2h / alerts 3h increases GitHub Actions usage
  (free for public repos) — confirm the pace is acceptable.

---

**Guardrails for any change** (see [`docs/goal.md`](docs/goal.md)): the
golden-set test passes on every PR and its floors only ever rise; crowd feedback
stays advisory; schema changes migrate the carried-forward `state.db` in place;
no paid dependencies without owner sign-off; one logical change per PR with green
CI; `ruff format` + `ruff check` + full `pytest` pass before every push.
