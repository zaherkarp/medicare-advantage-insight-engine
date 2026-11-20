# 01. News Retrieval Track: Decisions and Protocol

Written 2026-11-16, after the second Phase 0 assessment
(`00-repository-assessment.md`) was merged. This document records the owner's
decisions on that assessment and fixes the protocol for the first experiment
before any question, snapshot or retriever exists. Nothing here changes
production code.

---

## 1. Decisions recorded (owner, 2026-11-16)

1. **The news archive is a separate research track**, with its own corpus,
   questions, baselines and results. It is not only a secondary tier of the
   regulatory corpus.
2. **The news track goes first.** Checking that a story answers a query costs
   far less owner time than locating a passage in a 200,000-word rule, and it
   is the only track whose result maps directly onto something the site ships
   (its search box).
3. **The regulatory track (eCFR, Federal Register, CMS PDFs) is paused.**
   No corpus work starts on it. Resuming it means revisiting decisions 1 and 2
   in `00-repository-assessment.md` §12; decisions 3 to 5 there stay open.
4. **Questions are drafted by Claude and vetted by the owner**, item by item,
   before anything is frozen (§5).

The decisions on record from 2026-08-08 (`00-repository-assessment.md` §9)
still apply unchanged, including the paid-API exemption, offline CI, and the
Phase 10 gate.

---

## 2. Research question

**How much better than the deployed search can a transparent lexical ranker
do on realistic Medicare Advantage queries over the published news archive,
and on which kinds of query does each method fail?**

The deployed search (`static_export.py`, the inline `search()` function) keeps
a story only if every whitespace-separated query term is a substring of its
title, summary, source and category, and returns matches in index order,
which is newest first. It does no ranking. The question is not "is BM25
better than nothing" in the abstract; it is what the site's search should do,
measured against what it does today.

Embeddings are out of scope for the first round. They are added only if the
lexical results leave a gap that a measured failure pattern says embeddings
could close (§7).

---

## 3. Corpus

**Source.** The public `https://signal.zaherkarp.com/search-index.json`,
which needs no archive export and exposes nothing that is not already public.
It holds only stories above the archive floor (`archive_min_score: 0.1`) and
at most 2,000 rows, with five text fields per story: title, summary, source,
category, date. It is rewritten every two hours, so a snapshot is the only
reproducible form.

**Pinning.** One snapshot, recorded in a committed manifest:

| Field | Purpose |
|---|---|
| `url`, `fetched_at` (UTC) | Provenance |
| `sha256`, `bytes`, `rows` | Identity; every run records the snapshot hash it used |
| `deploy_commit` (if knowable) | Which scorer and floor produced the index |

**Storage: committed (owner decision, §9).** Because the live file changes
every two hours, the snapshot bytes cannot be re-fetched later; they must be
kept somewhere. The snapshot (about 750 KB) is committed under
`evals/news/`. The relevance holdouts already commit several hundred story
titles and summaries, so this is not a new kind of content in the repository,
and it is the only option that keeps a run reproducible from a clone alone.

**Cleaning, done in the research layer and recorded.** The production
pipeline is not changed.

- Strip the syndication tail (`The post <title> appeared first on <outlet>`
  and its truncated variants). It was in 37% of rows on 2026-11-03 and is
  correlated with source, so leaving it in lets a ranker match on outlet
  rather than content.
- Derive a stable story id from the `url` field (`/story/<item_id>.html`).
- Keep `date` as given, empty where empty (21% on 2026-11-03), and never
  substitute a fetch time for it.

**Duplicates: already removed by production, so no clusters (revised
2026-11-16, during the snapshot PR).** The plan above was to recompute
near-duplicate clusters over the snapshot. Two measurements on the first
snapshot (1,445 stories) replaced it:

- The index is already one story per production duplicate group:
  `static_export._search_index` reads `StateStore.get_stories` with its
  default `include_duplicates=False`, which hides every row with a
  `duplicate_of`.
- Re-clustering by title anyway did harm. With no time window it formed 46
  groups, the largest of 17, by merging distinct stories that share a title
  template: different payers' "sets quarterly dividend" items, different
  plaintiffs' suits against HHS, unrelated Federal Register notices titled
  "Agency Information Collection Activities". With production's 3-day window
  it formed 5 pairs, of which 2 were still wrong (CVS and Centene suits filed
  the same week). Those are exactly the entity confusions the questions are
  meant to test.

So judgments name **story ids**. Where two stories genuinely say the same
thing (a residual duplicate production missed), the question item groups
them into one unit (§5), decided per question during vetting rather than by a
global rule.

---

## 4. Conditions

All four are deterministic, stdlib-only, and need no new dependency. Every
condition after A0 runs through the same SQLite FTS5 index over
`title, summary` with the same tokenizer, so each step changes one thing.

| Id | Condition | What changes from the row above |
|---|---|---|
| **A0** | The deployed filter, reproduced exactly in Python: every term a substring of `title + summary + source + category`, lowercased; results in index order (newest first) | The production baseline |
| **A1u** | FTS5 with the current `_fts_query` (quoted prefix terms joined by AND); results in the same newest-first order as A0, no ranking | Matching only: fields and match rule change, order does not |
| **A1** | The same FTS5 match as A1u, ordered by `bm25()` | Ranking only: identical candidate set to A1u |
| **B** | FTS5 with the same quoted prefix terms joined by OR, ordered by `bm25()` | The conjunction only: same ranker and tokenizer as A1 |

A1u to A1 measures what ranking buys, on an identical candidate set. A1 to B
measures what requiring every term costs. The first assessment predicted the
conjunction drives recall toward zero on long queries; this is where that
prediction is tested.

**A0 to A1u is a combined matching change, not one factor.** It changes the
searched fields (A0 also matches `source` and `category`) and the match rule
(A0 matches substrings anywhere, so `health` hits `UnitedHealthcare`; FTS5
matches token prefixes, so it does not) at the same time. That step is
reported as such, with the queries whose candidate sets differ listed and each
difference attributed by hand to fields or to match rule. Splitting it into
two more conditions was considered and not done: neither intermediate form is
something the site would ship.

A0 is reproduced from the JavaScript, not imported from it. A unit test pins
it against a handful of hand-checked queries so the reproduction cannot drift
from the deployed behaviour unnoticed.

**Abstention.** A0, A1u and A1 abstain by returning nothing. B always returns
something whenever any term matches, so for unanswerable questions B is scored
on whether its top score falls below a threshold chosen on development data
and reported with the threshold. This asymmetry is part of the result, not
something to engineer away.

---

## 5. Questions

**Target:** about 30 questions, all in the development split. No held-out set
yet (§8).

**Types, chosen for what short news text can honestly test.** No temporal,
supersession or exact-numeric questions; the snippets cannot support them.

| Type | What it exposes |
|---|---|
| Entity ambiguity | Similar payer and plan names (for example "SCAN" the insurer against "scan" the procedure, which `golden_set.yaml` already guards in scoring) |
| Acronym | Acronyms that collide with words or with each other |
| Terminology drift | The same concept under different wording |
| Distractor-rich | Several lexically similar wrong stories, one right one in other words |
| Multi-story | A question answered only by two or more distinct units together |
| Natural-language length | A long question of the kind a person actually types, where the conjunction bites |
| Unanswerable | Nothing in the snapshot answers it |

**Item schema** (YAML, one file, content-hashed when frozen):

```yaml
- id: news-dev-001
  query: "..."                 # what the user would type
  question_type: entity_ambiguity
  answerable: true
  expected_answer: "..."       # null when answerable is false
  relevant:                    # units of story ids, graded
    - {stories: [3f2a...], grade: 2}            # directly answers
    - {stories: [9c1e..., 47b0...], grade: 2}   # two copies of one story: one unit
    - {stories: [b812...], grade: 1}            # related, partially answers
  distractors: [5e1d...]       # look-alike stories known NOT to answer; never scored as relevant
  failure_mode_targeted: "..."
  query_source: drafted        # drafted | owner
  vetting: pending             # pending | accepted | edited | rejected (rejected items are kept, not deleted)
  note: "..."
```

`expected_answer` and `distractors` were added when the first draft was
written (2026-11-19): the answer lets a vetter check the grades against what
the stories actually say, and the distractor list records the look-alike
stories each question was built around, for the failure analysis later.
Distractors are documentation only; scoring ignores them. Every item starts
`pending` and the set is frozen only when none is.

**Drafting and vetting protocol.**

1. Claude drafts each query and its relevant units by reading the cleaned
   snapshot, **before any retriever code exists in the repository**. No
   retriever is run, informally or otherwise, while drafting.
2. The owner vets every item against the stories themselves: accept, edit, or
   reject, recorded per item. Edits to grades or to the relevant set are kept
   as the label; the draft is not ground truth.
3. The owner adds queries of his own where he can, marked `query_source:
   owner`. There is no log of real site searches (the search runs in the
   reader's browser and GoatCounter records page views only), so owner-written
   queries are the closest thing to real demand the set will have. Results are
   reported for both sources separately as well as together.
4. When vetting is complete the set is frozen: content hash recorded, and
   only then does any retriever code land.

**Known weakness, stated.** The drafter and the eventual implementer are the
same agent. Freezing before code limits this but does not remove it: the
drafter knows in general terms what BM25 and substring matching do. Owner
vetting and owner-written queries are the counterweights. The report will say
so.

---

## 6. Metrics and statistics

- **Unit-level** Recall@1, @5, @10, @20 and MRR: a unit counts once,
  however many of its stories are returned, so returning two copies of one
  story earns nothing extra. Each is reported strict (grade 2 only) and
  lenient (grade 1 or 2), per §9.
- nDCG@10 over the graded judgments.
- Abstention accuracy on unanswerable items, per §4.
- Per-question results always published, alongside aggregates and the
  breakdown by question type.
- Paired comparisons only: exact McNemar on per-question hit/miss at k=10, and
  paired bootstrap intervals for MRR and nDCG. At n=30 a modest difference
  will usually be inconclusive; that is reported as inconclusive, not as a
  ranking.
- Every run records the snapshot hash, question-set hash, git commit,
  condition parameters, and wall time.

---

## 7. When embeddings enter

Only after A0, A1u, A1 and B are scored and their failures read. If the failures
concentrate in terminology drift or paraphrased distractors, a semantic
condition has a stated hypothesis and is added under the 2026-08-08 rules
(paid API only in the research package, cached by `(content_hash, model_id)`,
cost recorded). If they concentrate in entity or acronym questions, the next
step is lexical (field weighting, entity normalization through `payers.py`),
not embeddings. Choosing an embedding provider (decision 5 in `00` §12) is
deferred until then.

---

## 8. Held-out data

Not built in this round. When it is, it comes from a **later snapshot**, with
every question drawn from stories first published after the development
snapshot was taken. That gives a held-out set disjoint in time by
construction, the same discipline `evals/relevance/` uses for its holdouts.

---

## 9. Decisions on the protocol's open questions (owner, 2026-11-16)

1. **The snapshot is committed** to the repository under `evals/news/` (§3),
   so every result is reproducible from a clone.
2. **Two relevance grades:** 2 directly answers the query, 1 is related and
   partially answers it. nDCG@10 uses both grades. Recall@k and MRR are
   reported at two thresholds, strict (grade 2 only) and lenient (grade 1 or
   2), so a method cannot look good by surfacing only related stories.

---

## 10. Sequence of PRs

Each is its own PR, per `AGENTS.md` rule 2.

1. This protocol and the decision record. Docs only.
2. Snapshot, manifest and cleaning, with tests. No retrievers. (No cluster
   file: see §3.)
3. Drafted questions, opened as a draft PR for item-by-item owner vetting.
4. Freeze: content hash recorded once vetting is complete.
5. Conditions A0, A1u, A1, B and the scoring harness, then the first results.

Code follows the boundary in `00-repository-assessment.md` §8: library code
under `src/ma_signal_monitor/research/`, data and questions under
`evals/news/`, tests under `tests/research/`. No new runtime or research
dependency is needed for this track.
