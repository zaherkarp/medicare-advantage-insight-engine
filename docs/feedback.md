# Reader feedback

Feedback tunes what the monitor surfaces. It comes from two kinds of sources,
kept deliberately separate by **weight**:

- **Owner ("me-sourced")** — your own verdicts via the live web widget, the
  `ma-signal-feedback` CLI, and (later) ntfy action buttons. These are ground
  truth, recorded at **weight 1.0**.
- **Crowd** — reactions from visitors on the public static site, collected via
  giscus and pulled back with the GraphQL API. These are advisory signal,
  recorded at **weight < 1.0**. Crowd input can flag things for your review but
  never auto-changes scoring or sources on its own.

Everything lands in one append-only `feedback` table (an audit log — rows are
never mutated after insert), so a too-greedy signal can always be traced.

## The shared key

Every story has a stable `item_id` (a hash of `source_name|link`, minted in
`normalize.py`). It is the SQLite primary key, the static page filename
(`story/<item_id>.html`), and the giscus discussion term — so a verdict, a
static page, and a crowd reaction all join on the same id with no extra
plumbing.

## Channels

| Channel | Weight | How it's recorded |
|---|---|---|
| `local_web` | 1.0 | 👍/👎 + "wrong category" widget on the live app → `POST /feedback` |
| `ntfy` | 1.0 | 👍/👎 action buttons on ntfy alerts → `ma-signal-feedback ingest-ntfy` |
| `cli` | 1.0 | `ma-signal-feedback mark <item_id> <verdict> [category]` |
| `github` | 0.2 | giscus reactions pulled by `ma-signal-feedback ingest-github` |

Verdicts: `relevant`, `irrelevant`, `wrong_category`, `great`. For
`wrong_category`, the corrected category key is stored alongside — the highest
value signal for future keyword tuning.

## Live app widget

The story page shows a progressive-disclosure widget:

1. **Tier 0** — one-tap 👍 Relevant / 👎 Not relevant.
2. **Tier 1** (revealed after a vote, all optional) — "wrong category" with a
   topic picker, and "a great one".

Revisiting a story shows your last verdict (the 👍/👎 button comes back
pressed). A plain-language explainer lives at `/about-feedback`.

## ntfy action buttons (feedback at the moment of reading)

When `WEBHOOK_MODE=ntfy` and `NTFY_FEEDBACK_TOPIC` is set, each alert carries
👍 / 👎 buttons. Tapping one publishes `{item_id, verdict}` to a separate,
private feedback topic (an ntfy `http` action). Pull those votes in with:

```bash
ma-signal-feedback ingest-ntfy
```

This polls the feedback topic's JSON endpoint and records each vote as an owner
verdict (`channel="ntfy"`, weight 1.0), idempotent by ntfy message id
(`source_ref=ntfy:<id>`). ntfy caps notifications at 3 buttons, so alerts show
*View Source* + 👍 + 👎; richer corrections (wrong category) stay on the web
widget. Choose a hard-to-guess topic name — anyone who knows it could post.

## Crowd via giscus

Set these (all public — giscus exposes them client-side; get them from
<https://giscus.app> after enabling Discussions on the repo):

```
GISCUS_REPO=owner/name
GISCUS_REPO_ID=...
GISCUS_CATEGORY=General
GISCUS_CATEGORY_ID=...
GISCUS_THEME=light
```

When all of `GISCUS_REPO`, `GISCUS_REPO_ID`, and `GISCUS_CATEGORY_ID` are set,
`ma-signal-build` mounts a giscus thread on each static story page with
`mapping: specific` and the term set to the `item_id`. giscus creates one
Discussion per story whose **title is the `item_id`**.

### Pulling reactions back

```bash
GITHUB_TOKEN=<read-only Discussions PAT> ma-signal-feedback ingest-github
```

This reads each Discussion's reactions via the GitHub GraphQL API and maps
them to verdicts:

| Reaction | Verdict |
|---|---|
| 👍 `THUMBS_UP` | relevant |
| 👎 `THUMBS_DOWN` | irrelevant |
| 😕 `CONFUSED` | wrong_category |
| ❤️ `HEART` / 🎉 `HOORAY` / 🚀 `ROCKET` | great |
| 😄 `LAUGH` / 👀 `EYES` | ignored (low signal) |

Ingest is **idempotent**: each reaction is stored under
`source_ref = reaction:<databaseId>`, and a unique index on
`(channel, source_ref)` makes re-runs no-ops. The `GITHUB_TOKEN` is used only
here and is never exposed to the browser (unlike the `GISCUS_*` values).

Discussions whose title doesn't match a known story (e.g. ad-hoc threads in the
category) are skipped.

## Source-yield review

Separately from per-story feedback, the **Status** page (`/status`) shows a
per-source relevance-yield table: for each source, how many of its stories
cleared `MIN_RELEVANCE_SCORE`, the resulting yield, and its best-ever score
(worst performers first). A source is **flagged for review** once it has a fair
sample but still under-delivers:

- at least `SOURCE_REVIEW_MIN_SAMPLE` stories (default 25), **and**
- yield below `SOURCE_REVIEW_YIELD_FLOOR` (default 5%), **and**
- max score below `SOURCE_REVIEW_MAX_SCORE_FLOOR` (default 0.2).

The third clause spares a source that occasionally lands a strong hit. Flagging
is advisory — it surfaces the source with a reason like `0/30 relevant (0%), max
score 0.04`; you confirm the disable by editing `sources.yaml`. This formalizes
the manual "this feed never produces anything relevant" decision.

## Keyword-candidate mining

```bash
ma-signal-feedback mine-keywords
```

Turns owner verdicts into labels (relevant = positive, irrelevant = negative)
and ranks uni-/bi-grams by the **weighted log-odds ratio with an informative
Dirichlet prior** (Monroe, Colaresi & Quinn 2008). The z-score controls for
overall frequency, so distinctive terms — not just common ones — rise to the
top. It prints two lists with per-term doc counts:

- **Inclusion candidates** — over-represented in *relevant* stories and not
  already in the taxonomy (new keywords worth adding).
- **Exclusion candidates** — over-represented in *irrelevant* stories (terms
  worth treating as negative signal).

It needs a few labels of each class before it produces anything. Output is
advisory: you review the candidates and edit `taxonomy.yaml` by hand — nothing
auto-mutates config.

### Acting on exclusions

`taxonomy.yaml` has an `exclusions` block that the scorer reads:

```yaml
exclusions:
  hard: ["high school sports"]   # a match vetoes the item to score 0
  soft: ["webinar", "podcast"]   # each match subtracts scoring.exclusion_penalty
```

Hard vetoes are surgical kill-switches for unambiguous off-topic noise — a
hard-vetoed item is still **archived** (with an `exclusion_veto` reason), never
silently dropped. Soft terms nudge borderline items down (`exclusion_keyword`
reason). Move mined exclusion candidates here once you trust them.

## What consumes this (planned)

Still planned, in priority order: golden-set growth (every owner verdict is a
regression-fixture candidate) and a weekly crowd-vs-model disagreement digest.
Neither auto-mutates config — they feed review queues.
