# 00. Repository Assessment

**Phase 0 of the retrieval-research workstream, second pass (2026-11-03).**

The first pass was written on 2026-08-08 against commit `fd8a6d4` (PR #67). This
pass re-measures it against commit `900fc7a` and the live site at
`https://signal.zaherkarp.com/`, and records which of its claims still hold,
which were overtaken, and which were wrong. The 2026-08-08 text is in git
history; the owner decisions it recorded are carried forward in §9 unchanged.

Every number below comes with the command or file that produces it. Where a
number could not be measured from this session, it says so.

**Headline.** The first pass concluded that the production archive is a good
news-monitoring product and an unusable retrieval-research corpus, and that
corpus construction is a precondition for the research. Both conclusions
stand. Three things are new:

1. **None of the first pass's "Next" step was started.** In the 87 days since,
   no corpus, manifest, `research/` package or `[research]` extra exists, while
   `docs/goal.md` Guardrail 4 already cites `src/ma_signal_monitor/research/`
   as if it did. The repository's effort went to a different, query-independent
   evaluation programme (§2.3). That is a planning fact, not a criticism: it
   means this workstream competes with an active production loop for the same
   owner time.
2. **The deployed search is not BM25.** Since the engine went web-only, the
   public search is a client-side substring filter with no ranking (§2.1). The
   first pass's "FTS5 already gives BM25" is true of the local dev server only.
3. **The proposed corpus anchor was incomplete.** The Federal Register does not
   carry several of the documents where Medicare Advantage numbers actually
   live, and the eCFR offers a better ground truth for temporal questions than
   the Federal Register does (§5).

---

## 1. Current architecture

A deliberately zero-ML monitoring pipeline published as a static site.

| Fact | Value | Source |
|---|---|---|
| Source modules | 12,274 lines in `src/` | `git ls-files 'src/*.py' \| xargs cat \| wc -l` |
| Tests | 693 passing, offline, ~26 s | `python -m pytest -q` on `900fc7a` |
| Runtime dependencies | 4 (`requests`, `feedparser`, `python-dotenv`, `pyyaml`) | `pyproject.toml` |
| Configured sources | 95 (77 `rss`, 14 `sec`, 4 `litigation`) | `config/sources.yaml` |
| ML / LLM / vector libraries | none | grep of `src/`, `pyproject.toml` |

```
sources.yaml
  -> fetchers/{rss,sec,cms,litigation}   all delegate to rss.fetch_feed
  -> normalize    item_id = sha256(source|link)[:16]; summary cut to 500 chars
  -> dedupe       exact id -> seen_items; title Jaccard -> duplicate_of
  -> scoring      transparent additive keyword model (score_item, no query)
  -> eligibility  brief/alert/display/exclude tier gate (flag-controlled)
  -> classify     highest-weight matched category
  -> storage      SQLite, 9 tables + FTS5
  -> static_export -> GitHub Pages at signal.zaherkarp.com (every 2 h)
```

**Where the archive lives changed.** Until 2026-09-06 the deploy republished
`state.db` inside the site and re-downloaded it next run. Since then it
persists only in the GitHub Actions cache under the `state-db-` prefix
(`.github/workflows/deploy-pages.yml`). The cutover destroyed the archive on
2026-09-07 (14,799 stories to 96); it was restored from an old Pages artifact,
and `seed-archive-cache.yml` now makes that recovery repeatable
(`.github/workflows/seed-archive-cache.yml` header). Two consequences for
research:

- The archive is **not publicly downloadable** any more. A copy comes only from
  dispatching `export-archive.yml`, which uploads a one-day artifact. The one
  run on record (2026-10-17) has expired. This session did not dispatch it, so
  the full archive is unmeasured here.
- The archive has already been lost once. **No research corpus may depend on
  the Actions cache for its existence.** That reinforces the first pass's
  manifest-plus-builder design (§8).

The engineering conventions the first pass praised are intact and should be
inherited: measurement-justified constants in config, ratchet-only golden-set
floors, offline deterministic tests, in-place migrations of the carried-forward
DB, and now `AGENTS.md`'s one-concern-per-PR and never-edit-labels-to-fit rules.

---

## 2. Where retrieval concepts already exist

### 2.1 Two search paths, neither ranked in production

| Path | Where | Behaviour | Deployed? |
|---|---|---|---|
| Static search | `static_export.py:130` (`_search_index`), `:208` (JS) | Every whitespace term must be a **substring** of title + summary + source + category. Results in index order, which is `COALESCE(published_date, fetched_at) DESC` (`storage.py:858`). **No ranking at all.** | **Yes**, the only public search |
| FTS5 search | `storage.py:543` (`_fts_query`), `:551` | `"t1"* AND "t2"* AND ...` over `title, summary`, `ORDER BY rank` (FTS5 default is `bm25()`). | No. FastAPI dev server only |
| `/ask` NL search | `web/routes.py:827`, `query_parser.py:208` | Rule-based question to structured filters, then FTS5. | No. `static_export` does not map `/ask` |

The static index is capped at `_MAX_STORY_PAGES = 2000` rows
(`static_export.py:39`) and honours `archive_min_score: 0.1`
(`config/app.yaml:16`). It holds 1,291 rows today.

### 2.2 Reusable deterministic machinery

| Asset | Location | Research role |
|---|---|---|
| IDF, weighted cosine, Jaccard | `similarity.py` | Dependency-free sparse baseline pieces |
| Emergent story threads | `threads.py` | Average-linkage IDF-cosine clustering of stories into events. An existing **event layer**, relevant to multi-document and duplicate-aware evaluation |
| Canonical payer grouping | `payers.py` | Alias to organization mapping. Entity normalization for entity-ambiguity questions |
| Declared causal model | `causal.py`, `config/causal_model.yaml` | Hand-built category graph. A candidate knowledge-structure condition, not a retriever |
| `score_item` | `scoring.py` | Query-independent topical prior (see §6a) |
| Eligibility gate | `eligibility.py` | Query-independent MA-relevance tier; a second prior |
| `StateStore(read_only=True)` | `storage.py` | Candidate archive adapter; read-only safety was not enforced at the assessed revision (see below) |

**Safety correction (2026-11-13).** At the assessed revision `900fc7a`,
`read_only=True` still created directories, opened an ordinary writable SQLite
connection and initialized FTS tables. It was not a safe read-only boundary.
PR #93 subsequently repaired that production path; an archive adapter must use
the repaired version and verify non-mutation against a disposable exported
copy. This correction does not change the November 3 measurements or authorize
an archive export.

### 2.3 An evaluation programme already exists, for a different question

`evals/relevance/` (since 2026-08-16) is a careful, owner-labelled held-out
evaluation of **document relevance**: two holdouts (`holdout_2026-08.yaml`,
n=122; `holdout_2026-10.yaml`, n=204), content-hashed, disjoint by
construction, with recall floors pre-registered before scoring and a written
contamination note. Its result (noise in the surfaced stream 62% to about 22%
on the August holdout) is documented in `RESULTS_2026-08.md`.

It is the right **template** for discipline and the wrong **object** for
retrieval. Its labels answer "is this story MA-relevant?", a query-independent
binary. Retrieval ground truth is query by passage. Nothing in those files can
be reused as gold passages, and they must not be edited for this work
(`AGENTS.md` rule 4).

---

## 3. What data we possess

### 3.1 Measured: the public index

```bash
curl -fsSL https://signal.zaherkarp.com/search-index.json -o search-index.json
# 748,756 bytes, fetched 2026-11-03
```

This is the above-floor subset (`archive_min_score: 0.1`), not the archive.

| Metric | Value |
|---|---|
| Rows | 1,291 |
| Distinct sources | 51 |
| Summary length, mean / median / max | 335 / 371 / **500** chars |
| Rows with summary >= 1,000 chars | **0** |
| Mentioning "Medicare Advantage" | 256 rows, **105.8 KB** of title + summary |
| Mentioning "Medicare" | 522 |
| Syndication boilerplate ("appeared first on") | 472 (**37%**) |
| Empty date | 266 (**21%**) |
| Summary identical to title | 11 |
| `uncategorized` | 507 (39%) |
| Federal Register rows (Rules + Notices) | 48 |
| Dated 2026-07 through 2026-10 | 947 of 1,025 dated rows |

Compared with 2026-08-08, the MA-relevant snippet text roughly tripled (35.8 KB
to about 106 KB, though the two numbers are measured on different populations:
the first on the full archive, this on the floored index). Its character did not
change: snippets truncated at 500 characters, about four months of real
coverage, a thin pre-2026 tail of stale feed entries.

### 3.2 Unmeasured: the full archive

Row counts, entity coverage, duplicate clusters and the `published_date` versus
`fetched_at` format split were measured on the full DB in August and cannot be
re-measured without an export. To refresh them: dispatch "Export archive for
evaluation" on `main`, download `state-db` within a day, then run the queries in
the first pass (git history of this file).

### 3.3 Still true in code

- There is no body or full-text column. `normalize.py:133` truncates `summary`
  to 500 characters; `RawFeedItem.raw_content` and `.content_html`
  (`models.py:24,28`) are dropped at normalization.
- No fetcher follows an article link, parses a PDF, or calls a primary-source
  API. The Federal Register sources still request the API's RSS view
  (`config/sources.yaml:25-31`).
- `fetched_at` is still written naive UTC (`models.py:45`,
  `storage.py:424`) while `published_date` keeps its source offset, and
  ordering still compares `COALESCE(published_date, fetched_at)` as strings.
- Syndication boilerplate is still indexed and still source-correlated.

---

## 4. Is the current corpus adequate? No, unchanged.

| Question type | Status | Why |
|---|---|---|
| Temporal versioning | Impossible | About four months of coverage; no versioned documents |
| Supersession | Impossible | No older authoritative document to supersede |
| Exact numeric evidence | Impossible | 500-char snippets; no rule text or tables |
| Boundary conditions | Impossible | Same |
| Multi-document synthesis | Degenerate | Many "documents" are one wire story |
| Chunking / contextualization | Meaningless | Every document is shorter than a chunk |
| Source hierarchy | Partly viable | Only once a primary tier exists |
| Unanswerable / abstention | Viable | Any corpus supports it, which is why it is the least informative class |

The first pass's scale comparison still makes the point: one Federal Register
rule (document `2026-06600`, the CY2027 MA final rule) was measured at
1,588,388 bytes, about 15 times the entire MA-relevant text of today's public
index.

---

## 5. The corpus: what the first pass got right and what it missed

**Right.** Primary sources are where the answers live, the Federal Register
JSON API is free and keyless, and the repository already polls it through RSS.
Re-verified today: the same query returns `count: 1146` (1,136 in August), each
result with `document_number`, `effective_on` and `raw_text_url`.

**Missed, and it matters for the question set.**

1. **Several of the most-asked MA numbers are not in the Federal Register.**
   The annual Advance Notice and Rate Announcement (benchmark growth rates,
   risk-model and normalization factors, coding-pattern adjustment) and the
   Star Ratings Technical Notes and cut points are published as documents on
   cms.gov, not as Federal Register rules. A corpus of Federal Register rules
   alone would make an entire family of "exact numeric" questions unanswerable
   for reasons unrelated to retrieval. This is from domain knowledge; **the
   cms.gov URLs, their stability, and the PDF extraction quality were not
   checked in this session.**

2. **The eCFR is the better ground truth for temporal versioning.** The eCFR
   versioner API returns the codified text of 42 CFR as of any date, section by
   section. Measured:

   ```bash
   curl -sS 'https://www.ecfr.gov/api/versioner/v1/versions/title-42.json?part=422'
   # 200; >= 1,000 section-version records for Part 422 dated 2017 through 2026
   # (2017: 331, 2018: 100, 2019: 45, 2020: 97, 2021: 111, 2022: 72,
   #  2023: 73, 2024: 100, 2025: 27, 2026: 44). The response may be capped at
   # 1,000; that was not checked.
   ```

   Each record names a section (`422.1`), a date, and whether the amendment was
   substantive. That gives three things the Federal Register does not:
   dated versions of the same passage (real supersession), stable section
   identifiers that work as gold-passage IDs, and an answer to the first pass's
   threat 4 ("correct passage is ill-defined in long documents"). The codified
   section is the canonical location of a rule; the preamble passage that
   explains it is an acceptable alternate.

**Revised proposal: a tiered corpus, built from a committed manifest.**

| Tier | Content | Purpose | Cost |
|---|---|---|---|
| R1 | eCFR 42 CFR Part 422 (and selected Part 423), point-in-time snapshots at a fixed list of dates | Versioning, supersession, boundary conditions, gold passage IDs | Free API, plain XML/text |
| R2 | Federal Register MA rules, pinned by document number, including proposed-to-final pairs | Rationale, conflicting proposed vs final text, multi-document synthesis | Free API, plain text, very long documents |
| R3 | A small pinned set of CMS PDFs (Rate Announcements, Star Ratings Technical Notes, a few contract years) | Exact numeric and table questions | PDF extraction; tables are the hard part. **Owner decision** |
| N | The news archive, read-only via `StateStore`, boilerplate stripped | Source hierarchy, terminology drift, distractors | Already exists; needs an export |

Start with R1 and R2. R3 should wait for an extraction spike, because
flattened tables are a confound that would show up as "retrieval failure" on
precisely the numeric questions the research cares most about.

---

## 6. Where the master plan needs correcting

The plan asked for disagreement. Items (a), (d) and (g) restate the first pass;
the rest are new.

**(a) "Condition A: existing heuristic baseline" is still not a retriever.**
`score_item` takes no query. Its honest role is a query-independent prior fused
into ranking; the eligibility gate is a second such prior. Testing whether a
hand-built domain prior helps query-specific retrieval is a real experiment and
keeps the transparent scorer in play.

**(b) The real production baseline is weaker than the plan assumes.** The only
deployed search is the unranked substring filter in §2.1. The honest baselines
are therefore three, all reproducible in Python without new dependencies:
(A0) the deployed substring-AND filter, unranked; (A1) FTS5 with the current
AND-of-prefix query; (B) BM25 with OR semantics. The A1 versus B contrast
isolates the conjunction, which on 15-word research questions drives recall
toward zero by construction.

**(c) Thirty questions offer limited power for modest effects.** With paired
binary outcomes, an exact two-sided McNemar test needs at least 6 discordant
questions all favouring one system to reach p < 0.05 (2 x 0.5^6 = 0.031).
That pattern can occur within n=30, so comparative evidence is not impossible.
A 10-percentage-point hit@10 gap at n=30 is a net 3 questions; that arithmetic
also describes Recall@10 only when each query has one gold passage. Fractional
recall with multiple gold passages needs its own paired analysis. Fourteen
question types over 30 questions is about two per type, so per-type breakdowns
would be anecdotes. My recommendation remains to treat the first 30 as a
**development and failure-analysis set**, make no ranking claims from that
development set, and plan the held-out test split at roughly 100 or more
questions. Choose its size from a power analysis for the intended effect and
metric, not a universal minimum, and narrow the type taxonomy to categories
the corpus can actually exercise.

**(d) Phase 10 stays gated** at 300 or more questions with recorded successful
trajectories (owner decision, 2026-08-08).

**(e) Parametric memory is an uncontrolled confound in Phases 6 and 7.** A
current LLM already knows many MA facts (MOOP limits, Star Ratings rules, rule
history). An answer-level score cannot tell a retrieved answer from a recalled
one. The plan needs a **closed-book condition** (same model, no retrieval) as a
mandatory baseline, and questions that favour facts a model is unlikely to
know: version-specific details, recent effective dates, or which of two
conflicting versions applied on a given day.

**(f) Agentic search must be confined to the frozen corpus.** "Follow citations
or sources where possible" means live fetching, which makes runs
unreproducible and lets the corpus change under the eval. The agent's tools
should resolve only against the manifest-built corpus.

**(g) Ground truth has one annotator.** The relevance holdouts are owner
labelled. Retrieval gold passages will be too. Single-annotator labels have no
agreement estimate, so the report should state that and, where feasible, have a
second pass on a sample.

**(h) Rerankers carry a dependency decision.** A local cross-encoder means
`torch`, which conflicts with this repository's dependency posture even under
the research exemption; an API reranker is paid. This is an owner call, and it
should be made before Phase 4E rather than discovered there.

**(i) Model versions are a reproducibility hazard.** Hosted embedding and chat
models are retired. Runs must record model IDs and cache embeddings keyed by
`(content_hash, model_id)` (the first pass already said so), and the report
should state that an LLM-dependent result may not be reproducible after a model
is withdrawn.

**(j) The eval-set location should follow the repository's convention.**
`evals/` is already a top-level research harness package (`evals/relevance/`,
importable via `pythonpath = ["src", "."]`). Retrieval questions belong in
`evals/retrieval/`, not a new `evals/questions/` beside it.

---

## 7. What should stay untouched

- The ingestion path, `scoring.py`, `eligibility.py`, `digest.py`,
  `static_export.py` and every `ma-signal-*` entry point.
- `deploy-pages.yml` and the other workflows. A research change has no reason
  to touch `.github/`, and `AGENTS.md` rule 4 would force it into its own PR.
- `tests/fixtures/golden_set.yaml`, its floors, and both relevance holdouts.
- The `stories` schema. Guardrail 7 already forbids research writes to
  `state.db`.

---

## 8. Integration boundary

Unchanged from the first pass except for the eval location and an explicit
durability rule.

```
src/ma_signal_monitor/research/      one-way dependency: research -> app
    corpus/     models.py  manifest.py  ecfr.py  federal_register.py
                archive_adapter.py  store.py
    retrieval/  base.py  lexical.py  semantic.py  hybrid.py  rerank.py  metrics.py
    cli.py                            ma-signal-research
evals/retrieval/                      questions, schema, runner; content-hashed
evals/retrieval/fixtures/             small committed corpus slice for offline tests
experiments/runs/                     gitignored; small run manifests committed
tests/research/
```

- **The corpus is rebuilt from a committed manifest** (document numbers, eCFR
  section and date pairs, source URLs, SHA-256 of fetched content) into
  `data/research_corpus.db`, which `.gitignore` already covers. It never
  depends on the Actions cache.
- `archive_adapter.py` reads the news tier through
  `StateStore(path, read_only=True)` from an exported copy, using the repaired
  implementation described in §2.2 and verifying non-mutation.
- Research dependencies sit behind a `[research]` extra. `pip install .` and
  `pip install ".[web]"`, which the deploy uses, stay unchanged.
- Dependencies: stdlib for lexical work (BM25 is about 40 lines; FTS5 provides
  an independent cross-check); `numpy` at most for brute-force cosine at this
  scale. No LangChain, LlamaIndex or vector database unless a measurement shows
  the need.

---

## 9. Owner decisions on record (2026-08-08, carried forward)

- **Guardrail 4, lifted for research only.** Paid embedding and LLM APIs are
  allowed inside `src/ma_signal_monitor/research/` and the `[research]` extra,
  nowhere else. CI holds no API keys and uses no network; LLM-touching tests
  are fixture-mocked, and pytest stays offline and deterministic. Cache
  embeddings by `(content_hash, model_id)` so unchanged documents are not
  re-embedded. Every run records model IDs, token counts and estimated cost.
- **Sequencing: corpus, then protocol, then eval set.** Gold passages cannot be
  annotated before the documents exist. Anti-contamination holds because the
  corpus is built from a manifest while no retriever and no question exists,
  and the eval set is frozen and hashed before the first retriever is written.
- **Phase 10 gated** at 300 or more questions with recorded successful search
  trajectories.

---

## 10. Threats to validity

1. **Small n** (§6c). Expect most comparisons at n=30 to be inconclusive and
   report them that way.
2. **Parametric memory** (§6e). Without a closed-book baseline, answer-level
   results are uninterpretable.
3. **Annotator and implementer are the same people.** Freeze and hash the eval
   set before retrieval code exists; annotate from source documents, never from
   retriever output.
4. **Corpus selection bias.** Whoever picks the manifest picks what is
   answerable. Write the selection rule down (date range, document types,
   inclusion query) before choosing documents.
5. **Table and PDF extraction** (§5, R3) can masquerade as retrieval failure.
6. **Text hygiene.** Boilerplate is 37% of the public index and source
   correlated; strip it in the corpus layer and record that.
7. **Date handling.** The naive-UTC versus offset-aware split must be resolved
   in the research corpus layer before any temporal condition runs. Changing
   production ordering is a separate, user-visible change.
8. **Archive fragility.** The news tier exists in one cache entry and has been
   lost once. Pin the export used for any experiment by SHA-256.
9. **Single annotator** (§6g).
10. **No LLM judge as ground truth.** Rubrics, prompts and outputs are stored
    and human-sampled.

---

## 11. Production issues noticed, not fixed here

Each belongs in its own issue or PR, per `AGENTS.md` rule 2.

- **The public search index will start silently dropping old stories.**
  `_search_index` keeps the newest 2,000 above-floor rows. It holds 1,291 now,
  and above-floor volume was 202 to 281 per month from July to October, so the
  cap is likely reached in early 2027. After that, older stories vanish from
  site search with no signal. This is an inference from four months of volume.
- **Public search is unranked.** A multi-word query returns every matching
  story in date order. FTS5 ranking exists in the codebase but is not deployed.
- **Guardrail 4 cites a package that does not exist.** Harmless until someone
  reads it as an inventory.
- **The 2026-08-08 entity and state extraction defects** (a FEMA story tagged
  `WA` from a dateline; a Cambia story with no entities) could not be
  re-checked without an archive export.

---

## 12. Owner decisions needed before Phase 1

1. **Proceed, given competing priorities?** The workstream has been dormant for
   87 days while the relevance loop advanced. Recommended: proceed only if it
   gets dedicated time, since a half-built research layer has no value.
2. **Corpus scope.** Recommended: R1 (eCFR) and R2 (Federal Register) first;
   decide on R3 (CMS PDFs) after a one-document extraction spike.
3. **Annotation budget.** About 100 questions for a held-out split is real
   owner time, at the same labelling rigour as the relevance holdouts.
4. **Reranker dependency** (§6h): local `torch` or a paid API.
5. **Embedding provider.** Any provider behind one interface; the choice sets
   cost and the model-retirement risk.

---

## Next

Phase 1 remains **corpus construction**, now starting with an eCFR Part 422
manifest and a pinned Federal Register set. No retriever, embedding or
evaluation question is written until the corpus exists and its manifest is
committed.

### Not verified in this session

- Full-archive metrics (§3.2): needs an export.
- Whether the eCFR versions response is capped at 1,000 records.
- cms.gov document URLs, stability and PDF extraction quality (§5).
- Live behaviour of any fetcher; only the code was read and the test suite run.
