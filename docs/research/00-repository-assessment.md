# 00 — Repository Assessment

**Phase 0 of the retrieval-research workstream.** Written before any retrieval
code exists, so that the corpus and architecture decisions that follow are on
the record and falsifiable.

Measured against commit `fd8a6d4` and the live published archive as of
**2026-08-08**. Every number below is reproducible; the command that produces it
is given inline.

The headline finding is negative, and it is the reason this document exists:
**the production archive is a good news-monitoring product and an unusable
retrieval-research corpus.** Sections 3 and 4 give the measurements; section 5
gives the smallest defensible fix.

---

## 1. Current architecture

A deliberately **zero-ML** monitoring pipeline. ~12.5k LOC across 52 modules,
47 test files / 643 tests, running on four runtime dependencies (`requests`,
`feedparser`, `python-dotenv`, `pyyaml`). No numpy, no scikit-learn, no
embedding model, no LLM client, no vector store. A repo-wide grep for the major
ML/LLM libraries returns only guardrail prose and blocked backlog items.

```
sources.yaml (95 sources)
  → fetchers/{rss,sec,cms,litigation}   ← all four delegate to rss.fetch_feed
  → normalize   (item_id = sha256(source|link)[:16]; summary truncated to 500)
  → dedupe      (exact id → seen_items; title-Jaccard → duplicate_of)
  → scoring     (transparent additive keyword model, score_item)
  → classify    (highest-weight matched category)
  → drafting    (deterministic string assembly)
  → renderers   (ntfy / teams / generic)
  → storage     (SQLite: 9 tables + FTS5)
  → static_export → GitHub Pages
```

The archive DB is not in the repo. `deploy-pages.yml` downloads the previously
published `state.db` from GitHub Pages, ingests into it, and republishes it
inside the site. **GitHub Pages is the production datastore.** `data/` and
`*.db` are gitignored.

### Engineering conventions worth preserving

The repository holds a higher standard than most research code, and the research
subsystem should meet it rather than lower it:

- Config is comment-dense and **measurement-justified** — `config/app.yaml`
  embeds the threshold sweep that justifies `similarity_threshold: 0.13`;
  `taxonomy.yaml` embeds the off-domain rates (41.4% / 9.3% / 1.8%) behind
  `ma_context_min_priority: 5`. Constants come with the evidence that set them.
- `tests/fixtures/golden_set.yaml` is a **ratchet**: floors may only rise.
- Tests are fully offline and deterministic (`responses` for HTTP, fixed dates).
- Schema changes migrate the carried-forward production DB in place via a
  guarded `_ensure_column` / `PRAGMA table_info` pattern.

---

## 2. Where retrieval concepts already exist

| Asset | Location | Relevance |
|---|---|---|
| FTS5 index, `ORDER BY rank` | `storage.py:506` | **Already BM25.** SQLite FTS5's default rank *is* `bm25()`. |
| `_fts_query` | `storage.py:498` | Builds `"t1"* AND "t2"* AND …` — a strict conjunction over every query term. |
| `query_parser.parse_query` | `query_parser.py` | Rule-based NL question → structured filters (date / category / entity / state / score tier). |
| `similarity.py` | `similarity.py` | IDF weights, weighted cosine, token Jaccard. Tested, dependency-free. |
| `scoring.score_item` | `scoring.py:59` | Transparent additive relevance model — but see §6(a). |
| `StateStore(path, read_only=True)` | `storage.py` | Read-only mode already exists. |
| `_story_filters(...)` | `storage.py` | Shared filter-clause builder to mirror in new queries. |

Two observations that matter more than they look:

**FTS5 already gives BM25.** A "Condition B: implement BM25" experiment is
largely already shipped. The genuinely informative lexical experiment is not
*adding* BM25 but **relaxing the conjunction**: `_fts_query` ANDs every term
with a prefix wildcard, so a 15-word research question requires all 15 terms to
co-occur in a ≤500-character document. Recall approaches zero by construction.
AND-of-prefix vs. OR-with-BM25-ranking is a free, high-information comparison
against real production behavior.

**`score_item` is not a retriever.** It takes no query argument. It is a
*query-independent topical prior* over documents. It cannot be run against an
evaluation question at all, and treating it as a retrieval baseline would be a
category error. Its correct research role is as a **prior fused into ranking**
(§6a).

---

## 3. What data we actually possess

Measured directly against the published archive.

```bash
curl -fsSL https://zaherkarp.github.io/medicare-advantage-insight-engine/data/state.db -o /tmp/state.db
# sha256 f062c766428ebe31a1a724ff1d44deb98a0f50bdfe26cad8e2e05ac5567c1e70, 17,395,712 bytes
```

| Metric | Value | Query |
|---|---|---|
| Rows in `stories` | **8,090** | `select count(*) from stories` |
| Distinct sources | 78 | `select count(distinct source_name) from stories` |
| Summary length: mean / max | **306 / 500 chars** | `select avg(length(summary)), max(length(summary)) from stories` |
| Documents ≥ 1000 chars | **0** | `select count(*) from stories where length(summary)>=1000` |
| Mentioning "Medicare Advantage" | **108** | `... where title\|\|' '\|\|summary like '%Medicare Advantage%'` |
| Mentioning "Medicare" | 285 | as above with `'%Medicare%'` |
| **Total text of the 108 MA docs** | **35.8 KB** | `select sum(length(title)+length(summary)) ...` |
| Total text of the 285 Medicare docs | 110 KB | as above |
| `entities` non-empty | 176 (**2.2 %**) | `... where entities not in ('[]','')` |
| `primary_category = 'uncategorized'` | 7,774 (**96 %**) | `select count(*) ... group by primary_category` |
| Near-duplicate clusters | 445 | `select count(distinct duplicate_of) ...` |
| `fetched_at` range | **2026-07-18 → 2026-08-08** | `select min(fetched_at), max(fetched_at) from stories` |

**The archive is 21 days old.** Publication years of the 108 MA documents:
`2018:3  2020:1  2021:2  2022:1  2023:4  2024:2  2025:6  2026:89`. The pre-2026
rows are a handful of stale feed entries, not historical coverage.

There is no `content` / `body` / `full_text` column anywhere in the schema.
`summary` is the only body text, and `normalize.py:133` truncates it to
`max_summary_length = 500`. No code path follows an article link, extracts HTML,
parses a PDF, or calls a primary-source API — `RawFeedItem.raw_content` and
`.content_html` are populated by the RSS fetcher and then **dropped at
normalization**, used transiently only for outbound-link harvesting.

### Three defects inside the text itself

- **517 rows carry syndication boilerplate** — `… [...] The post <title>
  appeared first on Becker's Payer Issues | Payer News.` — inside the indexed
  `summary`. On 300-character documents that is a large token fraction, and it
  is *correlated with source*, so any retriever can score partly by learning
  which outlet published a document rather than what it says.
- **The highest-priority source has no usable dates.** All **22** CMS Newsroom
  rows (priority 5, the one tier exempt from the MA-context gate) have an empty
  `published_date`; 5 of 22 additionally have `summary` byte-identical to
  `title`.
- **Dates are compared as strings across incompatible formats.**
  `published_date` is stored timezone-aware (`2026-08-07T10:30:00-04:00`) while
  `fetched_at` is stored naive UTC (`datetime.utcnow().isoformat()`). Every
  query orders on `COALESCE(published_date, fetched_at)`, compared
  **lexicographically** in SQL. Mixed offsets therefore sort by
  wall-clock-plus-suffix, not by instant, and the **175** rows with no
  `published_date` silently pose as "published when we noticed them."

---

## 4. Is the current corpus adequate for the proposed research? No.

The research questions require recovering *specific evidence* — a threshold, a
cut point, a methodology change, an effective date. Against this corpus:

| Question type | Status | Why |
|---|---|---|
| Temporal versioning | **Impossible** | 21 days of ingestion. Nothing to version across. |
| Supersession | **Impossible** | No older authoritative document present to be superseded. |
| Exact numeric evidence | **Impossible** | 500-char truncation; no rule text, no tables. |
| Boundary conditions | **Impossible** | Same. |
| Multi-document synthesis | **Degenerate** | 445 duplicate clusters over 108 MA docs — "multiple documents" mostly means the same wire story. |
| Chunking / contextualization | **Meaningless** | Every document is already ≤500 chars, i.e. smaller than a chunk. |
| Source hierarchy | Partially viable | Requires a primary tier that does not currently exist. |

### The scale problem, stated plainly

A single Federal Register document — the CY2027 MA final rule, document
`2026-06600` — is **1,588,388 bytes / 227,520 words**:

```bash
curl -sS https://www.federalregister.gov/documents/full_text/text/2026/04/06/2026-06600.txt | wc -c
```

The entire Medicare-Advantage-relevant text of the production archive is
**35.8 KB**. **One rule is ~43× the whole MA corpus** (~14× if the comparison is
widened to every document mentioning "Medicare" at all).

**Conclusion — a decision, not a hypothesis.** Running BM25 vs. embeddings vs.
hybrid vs. agentic search over 108 snippet documents would produce differences
indistinguishable from noise at n≈30 questions. That outcome is *worse than no
result*, because it would be published as one. Corpus construction is therefore
a **precondition** for the research programme, not a middle phase of it.

---

## 5. The smallest defensible corpus expansion

**This is not a new source. It is a source the repository already ingests, read
properly.**

`config/sources.yaml` already configures `Federal Register - CMS Rules`
(priority 5) and `Federal Register - CMS Notices` (priority 4), pointed at
`federalregister.gov/api/v1/documents.**rss**?…`. The repository is already
calling the Federal Register API — it simply requests the RSS teaser view, which
carries no abstract and no body. Requesting `.json` from the same endpoint and
following `raw_text_url` is a **format change on an already-configured source**.

Verified reachable from CI-equivalent network conditions:

```bash
curl -sS 'https://www.federalregister.gov/api/v1/documents.json?conditions%5Bterm%5D=%22Medicare+Advantage%22&conditions%5Bagencies%5D%5B%5D=centers-for-medicare-medicaid-services&per_page=1'
# → {"count": 1136, …}
```

Free, no API key, no auth, no scraping. Each result carries `document_number`,
`title`, `publication_date`, **`effective_on`**, `type` (Rule / Proposed Rule /
Notice), and `raw_text_url` for the complete document.

Why this specific corpus:

- **It is where the answers live.** Thresholds, cut points, risk-adjustment
  methodology, MOOP limits, benchmarks, effective dates.
- **It makes the hard question types possible.** `publication_date` +
  `effective_on` + the CY-numbered rule series give real **temporal
  versioning**; correction/amendment relationships give real **supersession**;
  the Proposed Rule → comment → Final Rule chain gives real **conflicting
  sources** and **multi-document synthesis**.
- **It is reproducible.** U.S. Government works, public domain, stable document
  numbers.
- **It makes chunking a real problem** — 227k-word documents require it.

Proposed initial corpus: **~150–300 curated CMS MA documents**, pinned by
document number, spanning enough contract years to support versioning questions.
The existing news archive is retained as a **secondary tier** — which is exactly
what makes *source hierarchy* questions real: a trade article paraphrases a
threshold that the Final Rule states exactly.

`CourtListener` (2 sources) has the identical shape — RSS search feed rather
than the API — so no filing text is ever retrieved. Same fix available, not
proposed for the initial corpus.

---

## 6. Where the original research plan needs correcting

Recorded because the plan was explicit that disagreement was wanted.

**(a) "Condition A: the existing heuristic baseline" is not a retriever.**
`score_item` takes no query (§2). Replace with two distinct conditions:
Condition A = `query_parser` → structured filter → FTS5, the *real* existing
search path; and separately, `score_item`'s output as a **query-independent
prior fused into ranking**. The second is the more interesting experiment —
*does a hand-built domain prior help or hurt query-specific retrieval?* — and it
preserves the existing transparent scorer as a genuine baseline rather than
discarding it.

**(b) Conditions A and B partly collapse**, since FTS5 already ranks by BM25
(§2). The informative lexical experiment is the conjunction fix.

**(c) `golden_set.yaml` is a partial precedent, not a template.** It is better
than it first appears: 98 entries (41 relevant / 57 irrelevant), a section mined
from the production archive under adversarial double-review, with removal
provenance commented inline, and ratchet-only floors at 0.95/0.95. The
discipline should carry over. The *schema* should not: it labels **topical
relevance of a document**, a query-independent binary, whereas retrieval ground
truth is **query × passage**. Different annotation object.

**(d) Phase 10 (model adaptation) is not reachable at current scale** and is
gated — see §8.

**(e) The corpus cannot be committed.** `data/` and `*.db` are gitignored and
the corpus is ~hundreds of MB. Reproducibility requires a **manifest + builder**
(document numbers + SHA-256 content hashes), not a committed blob.

**(f) Unrelated production bug, worth its own ticket.** Entity and state
extraction are visibly wrong. Row `5cd988e995110194` — a *Utah News Dispatch*
story about FEMA — is tagged `states: ["WA"]`, almost certainly from matching
"WASHINGTON" in the dateline; a Becker's story explicitly about *Cambia Health
Solutions* has `entities: []`. Entity coverage overall is 2.2 %. Not fixed here;
noted so that metadata-conditioned experiments do not silently inherit it.

---

## 7. Proposed boundary between production and research code

**Hard rule: the research subsystem never writes to `state.db` and never imports
into the ingestion path.** Guardrail 3 makes any schema change to `stories` a
production risk for zero research benefit.

```
src/ma_signal_monitor/research/       # one-way dependency: research → app
    corpus/     models.py  build.py  federal_register.py  archive_adapter.py  store.py
    retrieval/  base.py  lexical.py  semantic.py  hybrid.py  rerank.py  metrics.py
    eval/       schema.py  runner.py
    cli.py                            # ma-signal-research

evals/questions/*.yaml                # versioned + content-hashed, dev/val/test splits
evals/fixtures/                       # small committed corpus slice for offline tests
experiments/runs/                     # gitignored; run manifests committed
tests/research/
```

- Corpus in a **separate** SQLite file (`data/research_corpus.db`), gitignored,
  rebuilt from the committed manifest.
- `archive_adapter.py` reads `stories` via the existing
  `StateStore(..., read_only=True)`.
- Research dependencies behind a `[research]` extra, so `pip install .` and the
  production deploy are unchanged.
- Existing `ma-signal-*` entry points, `deploy-pages.yml`, and all 47 test files
  are untouched.

**Dependencies.** Stdlib-only through the first retrieval phases: BM25 is ~40
lines, `similarity.py` already provides IDF, and FTS5 supplies a second
independent BM25 for cross-checking. Semantic retrieval needs `numpy` at most —
300 documents × ~2,000 chunks is a 600k-float matrix, where brute-force cosine
is milliseconds. **No LangChain, no LlamaIndex, no vector database** unless and
until a measurement shows one is needed.

### Data-model changes

**None to production.** The research corpus needs its own document/chunk model
(stable document ID, source tier, URL, publication *and* effective date,
document type, parent document, chunk ID and position, raw and normalized text,
content hash, supersession metadata). It lives entirely in
`research/corpus/models.py`.

---

## 8. Owner decisions recorded (2026-08-08)

**Guardrail 4 — lifted for research only.** `docs/goal.md` prohibited paid
dependencies including LLM APIs without an explicit owner decision, and
`TODO.md` listed "Semantic / LLM scoring" as blocked. Paid embedding/LLM APIs
are now permitted **inside `src/ma_signal_monitor/research/` and the
`[research]` extra only**. Production ingestion, scoring, drafting, delivery,
and the deploy pipeline remain free and local; Guardrail 4 stays binding there.
`docs/goal.md` is amended accordingly in this commit.

Consequences that follow:

- CI holds no API keys and no network. Every LLM-touching test is
  fixture-mocked; `pytest` stays offline and deterministic.
- Embeddings are cached keyed by `(content_hash, model_id)`, so unchanged
  documents are never re-embedded and a rerun is free.
- Every run records model IDs, token counts, and estimated cost, so the price of
  each condition is a reported result rather than a hidden footnote.

**Phase sequencing — corpus first.** The original order (protocol → eval set →
corpus) is replaced by **corpus → protocol → eval set**. Gold passages cannot be
annotated before the documents containing them exist. Anti-contamination is
fully preserved: the corpus is built from a manifest of Federal Register
document numbers while **no retrieval code and no evaluation question exists**,
so it cannot be shaped toward either, and the eval set is still frozen and
content-hashed before the first retriever is written.

**Phase 10 — in scope, gated.** Model adaptation remains on the roadmap but may
not begin until the evaluation set reaches **≥ 300 questions with recorded
successful search trajectories**. At ~30 questions there is no plausible
training signal, and starting earlier would be the "call a prompt change
training" failure the plan explicitly warns against. The precondition is stated
numerically so the gate is falsifiable rather than a judgment call.

---

## 9. Threats to validity

1. **Corpus recency bias.** The news tier spans 21 days; any question drawing on
   it is temporally degenerate. Build questions on the Federal Register tier.
2. **Small n.** ~30 questions cannot separate systems differing by a few points.
   Use paired methods (bootstrap / McNemar), report intervals, and expect most
   comparisons to be **inconclusive** — report that rather than a ranking.
3. **Annotator–implementer contamination.** Freeze and content-hash the eval set
   before retrieval code exists; annotate gold passages from source documents,
   never from retriever output.
4. **"Correct passage" is ill-defined in long documents.** A Federal Register
   rule repeats the same provision across the preamble, the regulatory-impact
   analysis, and the codified text. Without a defensible definition, recall
   metrics become arbitrary.
5. **Text hygiene is a confound.** Syndication boilerplate is source-correlated
   (§3); normalize it out during corpus construction and record that it was done.
6. **The date bug (§3) must be fixed in the research corpus layer** — not in
   `storage.py` — before any temporal condition runs, or "temporal correctness"
   measures the bug rather than the retriever. Changing production ordering is a
   separate, user-visible change.
7. **Production metadata is unreliable** (§6f). Entity coverage is 2.2 %, and
   there are no contract IDs (H####), plan IDs, or county/FIPS codes anywhere —
   so entity-ambiguity questions have very little metadata to bite on.
8. **No LLM judge as ground truth.** Non-negotiable. Judge rubrics, prompts, and
   outputs are stored, and results are human-sampled.

---

## 10. Two observations on the existing docs

Neither is a research problem; both are recorded so this assessment does not
propagate a wrong number.

**`docs/goal.md`'s scorecard is not stale — it is a frozen baseline.** Its S1–S3
rows read 20 entries and 0.80/0.80 floors, which is correct for the stated
`Baseline (2026-07-03)` column; the file explicitly delegates the current
snapshot to `docs/loop.md`. Worth noting instead: **the S1 and S3 targets have
already been met.** The fixture now holds 98 entries (41 relevant / 57
irrelevant) against a target of ≥ 80, and CI floors are 0.95/0.95 against a
target of 0.90/0.90.

**`docs/loop.md`'s current snapshot has drifted.** Its scorecard row records the
golden set at 88 entries; the fixture holds 98. Left unedited here — the loop's
iteration log is its own durable memory and the loop is paused — but it should
be reconciled when the loop resumes.

---

## Next

Phase 1 is **corpus construction** (§5), not retrieval implementation. No
retriever, no embedding, and no evaluation question is written until the corpus
exists and its manifest is committed.
