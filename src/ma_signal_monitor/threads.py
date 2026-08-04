"""Emergent story threads for the layered timeline (the /timeline/threads lane).

Where the topic strip groups the window into the six *declared* taxonomy rows,
this groups it into *emergent* ones: it clusters the window's stories into
"threads" on the fly, names each thread from its own distinctive language, and
places it on the declared causal model — so the lane reads as the real stories
of the window flowing down the cause → effect cascade, not a fixed set of
buckets. This is the on-the-fly-categorization pattern of streaming news
aggregators (cluster the stream into emergent events, then label them), kept to
the app's guardrail: deterministic, no ML, no embeddings, no network.

The pieces are all reused primitives:

* **Cluster** — title + entity tokens, scored with IDF-weighted cosine
  (:func:`ma_signal_monitor.similarity.weighted_cosine`) rather than plain
  Jaccard, so window-ubiquitous words ("medicare", "advantage" — over half the
  window, in practice) barely move the score while a thread's own
  discriminating vocabulary does. Candidate pairs still come from a
  shared-term inverted index with rare-term blocking, so the pairwise work
  stays well below the naive all-pairs cost. Merging is *average*-linkage with
  a merge guard (mean cross-cluster similarity must itself clear the
  threshold), not single-linkage — single-linkage lets one bridging story
  transitively chain two unrelated threads into one; see :func:`_cluster`.
* **Label** — the terms most *distinctive* of a thread versus the rest of the
  window, via the keyword-mining n-gram + weighted-log-odds machinery gated by
  an in-thread-share floor (:func:`ma_signal_monitor.keyword_mining._terms` /
  ``distinctive_terms``) — see :func:`_label` for why confidence (bare
  log-odds) and distinctiveness (this) are different things.
* **Place** — the thread's dominant taxonomy category mapped onto the declared
  causal layers through :mod:`ma_signal_monitor.causal`.

Pure data-in/data-out so it is unit-testable without HTTP, mirroring
:mod:`ma_signal_monitor.angles`.
"""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime

from ma_signal_monitor.causal import layer_map, lookup_edge
from ma_signal_monitor.classify import get_category_label
from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.keyword_mining import _terms, distinctive_terms
from ma_signal_monitor.payers import ALIAS_TO_GROUP
from ma_signal_monitor.similarity import (
    idf_norm,
    idf_weights,
    title_terms,
    weighted_cosine,
)

# A thread label shows at most this many distinctive terms (joined by " · ").
LABEL_TERMS = 2
# A term must carry at least this share of its combined in-thread +
# rest-of-window occurrences (pos / (pos + neg)) to be eligible as a label
# term -- see keyword_mining.distinctive_terms. Without this floor, a
# window-ubiquitous phrase like "medicare advantage" out-ranks a thread's own
# distinctive language on raw statistical confidence alone, because
# confidence grows with count and boilerplate has the most count.
_LABEL_MIN_SHARE = 0.5
# Hard ceiling on stories fed to the pairwise clusterer — a safety valve, like
# the timeline's TIMELINE_MAX_STORIES fetch cap. Above it, only the top stories
# by relevance are threaded and the rest fold into "ungrouped" (surfaced, never
# silently dropped).
#
# Held at 1500 through the move to IDF-weighted cosine + average-linkage,
# but the cost profile did change. Candidate generation (the inverted index +
# rare-term blocking) is unchanged and was never the bottleneck; what got more
# expensive is merging. Single-linkage union-find is near-O(candidate pairs);
# average-linkage's merge guard needs each live cluster's running
# similarity-sum to every other cluster it borders (``links`` in
# ``_cluster``), and merging two clusters costs O(degree) to re-key those
# sums onto the merged cluster. In the worst case that arises for real MA
# windows -- one dominant, densely-overlapping news cycle (a single big CMS
# announcement covered near-identically by many outlets), the same pathology
# the merge guard exists to prevent -- a cluster's degree grows with its
# size, so total merge cost approaches O(n^2), not the O(n) union-find of the
# old code.
#
# Measured two ways, because the two disagree by ~5x and only one of them
# resembles production traffic:
#
#   n stories   adversarial (tiny shared vocabulary,   realistic (real MA
#               maximal candidate density)             headlines, ~9.8 terms/story)
#   500         ~0.44s                                 ~0.13s
#   1200        --                                     ~0.59s
#   1500        ~5.4s                                  ~0.98s
#   2000        --                                     ~1.95s
#
# The cap stays at 1500 (its pre-average-linkage value) on the realistic
# column, for a correctness reason that outweighs the adversarial one: this
# cap is not free headroom, it is a *silent truncation* -- everything past it
# is diverted straight into the "Ungrouped signals" row. ``routes`` fetches up
# to ``TIMELINE_MAX_STORIES`` (5000) per window.
#
# Measured against the real archive (not the ~595 figure from docs/loop.md
# iteration 5, which predates later rescoring): 6,701 stories total, **463**
# at/above ``archive_min_score`` and non-duplicate, 382 of them inside the
# default 30-day window. So a 500 cap would not truncate an "All" window
# *today* -- but at 463/500 it sits at 93% of the cap, one active news month
# from silently burying stories on the widest view, and the archive only
# grows. 1500 leaves ~3.2x headroom instead of 8%.
#
# The adversarial 5.4s case needs a 1500-story window whose headlines share a
# tiny vocabulary; that is an ingest pathology, not normal coverage, and the
# merge guard is what stops it becoming one giant thread. If it is ever
# observed for real, the fix is algorithmic (cap cluster degree, or fall back
# to single-linkage above some size), not a lower cap that quietly hides
# stories.
MAX_CLUSTER_INPUT = 1500
# A blocking term appearing in more than this fraction of the window is too
# common to discriminate threads, so it is not used to propose candidate pairs
# (classic rare-term blocking — this is what bounds the pairwise work).
_DF_BLOCK_FRACTION = 0.4
# A term must appear in at least this many window docs to be eligible as a label
# (drops one-off noise from the log-odds ranking).
_LABEL_MIN_DF = 2
# Sentinel layer order for a thread whose dominant category sits in no causal
# layer (or when no causal model is loaded) — sorts it below every placed layer.
_NO_LAYER_ORDER = 10_000


@dataclass(frozen=True)
class Thread:
    """One emergent cluster of related stories in the window.

    ``key`` is the thread's **anchor**: the stable ``item_id`` of its
    highest-relevance member (``stories[0]`` — see ``build_threads``' member
    ranking). Threads are never persisted — ``build_threads`` recomputes them
    fresh on every request from whatever window the store returns at that
    moment — so a positional key (an index into that request's clustering)
    would point at a different thread, or nothing, on the very next ingest
    cycle. Hashing the full membership would be just as fragile the other
    way: any new story joining the thread would change the hash, breaking the
    link on nearly every ingest. The anchor survives both: new stories joining
    the thread never move it, and it only changes when a higher-relevance
    story joins or the anchor itself ages out of the window.

    ``label`` is the thread's on-the-fly name — its most distinctive terms, or
    the dominant taxonomy label when the cluster is too small/uniform to name.
    The ``layer_*`` fields place the thread on the declared causal model via its
    dominant category; ``layer_order`` drives the upstream → downstream row order
    (``_NO_LAYER_ORDER`` when the thread can't be placed). ``mixed`` is True
    when the thread spans several categories with no clear majority among
    them — see ``_category_split`` — in which case ``dominant_category`` and
    every ``layer_*`` field are left empty/sentinel rather than guessing a
    placement from a coin-flip tie-break.
    """

    key: str
    label: str
    stories: tuple[dict, ...]
    dominant_category: str
    mixed: bool
    layer_key: str
    layer_short: str
    layer_label: str
    layer_order: int

    @property
    def total(self) -> int:
        return len(self.stories)


def _story_terms(story: dict) -> set[str]:
    """Clustering token set: headline content words plus one token per payer group.

    Title tokens carry the topic and are kept exactly as ``title_terms`` emits
    them — stripping payer name-words (e.g. "cvs", "health") out of the title
    was tried and measured *worse* (more fragmentation), because those words
    carry real topical signal beyond just naming the company.

    Entities are folded through ``payers.ALIAS_TO_GROUP`` rather than used as
    raw alias strings, for two reasons detection doesn't have to care about but
    clustering does:

    * **Fragmentation** — ``payers.py`` keeps aliases intentionally granular
      so *detection* fires on whichever wording a story uses ("UnitedHealthcare",
      "UnitedHealth", "UHC", "Optum" all independently match). Clustering wants
      the opposite: two stories about the same company should merge even when
      the outlets picked different aliases, so each alias is mapped to its
      canonical group and only the group identity becomes a token.
    * **Triple-counting** — a raw multi-word alias like "CVS Health" added
      as-is duplicates work ``title_terms`` already did: the headline
      contributes "cvs" and "health" as separate tokens, and the alias string
      then adds a third ("cvs health"), giving one company mention 3x weight
      in the Jaccard.

    The token is the opaque, ``@``-prefixed group slug (e.g. ``"@unitedhealthcare"``)
    so it can never collide with a title token — ``_TOKEN_RE``
    (keyword_mining.py) never emits ``@``. An alias with no group (not a
    watched payer) falls back to the previous lowercased-string behavior.

    Taxonomy categories are left out on purpose — they are the *coarse*
    grouping this lane exists to go beneath, so letting them merge would just
    rebuild the topic rows.
    """
    terms = set(title_terms(story.get("title") or ""))
    for alias in story.get("entities") or []:
        if alias:
            group = ALIAS_TO_GROUP.get(alias)
            terms.add(f"@{group.slug}" if group else alias.lower())
    return terms


def _cluster(
    term_sets: list[set[str]], threshold: float, *, entity_weight: float = 1.0
) -> list[list[int]]:
    """Average-linkage clusters of story indices by IDF-weighted cosine.

    Two invariants from the original single-linkage version are preserved
    unchanged:

    * **Candidate generation.** Candidate pairs still come from a shared-term
      inverted index with rare-term blocking (ubiquitous terms are skipped as
      blocking keys), so the pairwise work stays bounded well below the naive
      all-pairs cost. IDF weights are computed once for the whole ``term_sets``
      window (:func:`~ma_signal_monitor.similarity.idf_weights`), not per
      pair; likewise each document's vector norm
      (:func:`~ma_signal_monitor.similarity.idf_norm`) is computed once and
      reused. ``entity_weight`` (``config.thread_entity_weight``) scales the
      IDF of ``@``-prefixed payer-group tokens only (see ``_story_terms``),
      so payer identity's pull on clustering is tunable independent of title
      vocabulary; ``1.0`` (the default) leaves IDF untouched.
    * **Determinism.** Every candidate pair is scored once, then merges are
      applied greedily in descending similarity, ties broken on
      ``(min(i, j), max(i, j))`` — a total order over a fixed list, so the
      result never depends on dict/set iteration order (verified by
      ``tests/test_threads.py::test_clustering_is_order_independent``).

    What changed is the merge rule itself, to fix single-linkage chaining: a
    single bridging story (``A~B``, ``B~C``, ``A`` and ``C`` themselves
    unrelated) used to be enough to transitively merge ``A`` and ``C`` into
    one cluster. Here, merging clusters ``P`` and ``Q`` requires their
    *average* inter-cluster similarity — the mean similarity over all
    ``|P| x |Q|`` cross pairs, with any non-candidate pair (no shared
    unblocked term) counted as ``0.0`` — to itself clear ``threshold``, not
    just the one pair that proposed the merge. A single bridge story can no
    longer drag two large, mostly-dissimilar clusters together: it only pulls
    the average up by ``1 / (|P| x |Q|)`` worth of weight.

    Implementation: rather than recomputing the full cross-cluster average
    from scratch after every merge (the naive O(n^2)-per-merge approach), each
    live cluster keeps a running similarity-sum to every other cluster it
    shares a candidate-pair edge with (``links``). Merging ``P`` and ``Q``
    combines their edge lists by addition — ``sum(P|Q, X) == sum(P, X) +
    sum(Q, X)`` — which costs O(degree(P) + degree(Q)), so total work across
    every merge is amortized against the number of candidate-pair edges
    rather than the number of stories squared (see ``MAX_CLUSTER_INPUT``'s
    comment for the worst-case density this bound still doesn't save you
    from, and why that constant was lowered).

    Returns index groups (each ordered ascending); every input index lands in
    exactly one group, singletons included.
    """
    n = len(term_sets)
    if n == 0:
        return []

    weights = idf_weights(term_sets)
    if entity_weight != 1.0:
        weights = {
            t: (w * entity_weight if t.startswith("@") else w)
            for t, w in weights.items()
        }
    norms = [idf_norm(ts, weights) for ts in term_sets]

    df: Counter = Counter()
    for ts in term_sets:
        df.update(ts)
    df_cap = max(3, int(_DF_BLOCK_FRACTION * n))

    index: dict[str, list[int]] = {}
    for i, ts in enumerate(term_sets):
        for t in ts:
            if df[t] <= df_cap:
                index.setdefault(t, []).append(i)

    # Score every candidate pair exactly once (i < j: postings within a term
    # are appended in ascending i, so a_pos < b_pos already implies i < j).
    pair_sims: dict[tuple[int, int], float] = {}
    for members in index.values():
        for a_pos in range(len(members)):
            i = members[a_pos]
            for b_pos in range(a_pos + 1, len(members)):
                j = members[b_pos]
                if (i, j) in pair_sims:
                    continue
                pair_sims[(i, j)] = weighted_cosine(
                    term_sets[i], term_sets[j], weights, norms[i], norms[j]
                )

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path halving
            x = parent[x]
        return x

    size = {i: 1 for i in range(n)}
    # links[root][other_root] = summed weighted_cosine of every candidate pair
    # currently spanning the two clusters. Undirected: stored under both
    # endpoints for O(1) lookup. A cluster pair absent from this dict has no
    # candidate-pair edge between them, i.e. their whole cross-similarity is
    # implicitly 0.0 -- exactly the "absent pairs count as 0.0" contract.
    links: dict[int, dict[int, float]] = {i: {} for i in range(n)}
    for (i, j), sim in pair_sims.items():
        links[i][j] = links[i].get(j, 0.0) + sim
        links[j][i] = links[j].get(i, 0.0) + sim

    # Fixed, deterministic merge order: descending similarity of each
    # candidate pair's ORIGINAL (pre-merge) score, ties broken by (i, j) --
    # never by dict/set iteration order.
    order = sorted(pair_sims, key=lambda p: (-pair_sims[p], p[0], p[1]))

    for i, j in order:
        ri, rj = find(i), find(j)
        if ri == rj:
            continue
        avg = links[ri].get(rj, 0.0) / (size[ri] * size[rj])
        if avg < threshold:
            continue

        lo, hi = (ri, rj) if ri < rj else (rj, ri)  # smaller index stays root
        parent[hi] = lo
        size[lo] = size[ri] + size[rj]

        hi_links = links.pop(hi)
        del hi_links[lo]  # the lo<->hi edge is now internal to the merged cluster
        lo_links = links[lo]
        del lo_links[hi]
        for neighbor, s in hi_links.items():
            lo_links[neighbor] = lo_links.get(neighbor, 0.0) + s
            neighbor_links = links[neighbor]
            neighbor_links[lo] = neighbor_links.get(lo, 0.0) + neighbor_links.pop(hi)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _category_split(stories: list[dict], config: AppConfig) -> tuple[str, bool]:
    """Dominant category (if any holds a clear majority) plus a mixed flag.

    Counts each story's real ``primary_category`` (``uncategorized`` stories
    don't count). Ties among counts break toward the higher taxonomy weight
    (mirroring ``classify_item``), then the key, so the choice is
    deterministic. The count leader only wins as ``dominant`` when it holds
    *more than half* of the **categorized** stories — a payer-bucket thread
    spanning several categories with no clear majority would otherwise be
    placed on an arbitrary causal layer, a coin flip the causal links in
    :func:`build_thread_links` would then inherit.

    The denominator is deliberately the categorized count, not
    ``len(stories)``, because "mixed" and "sparse" are different conditions
    and only the first should suppress placement:

    * **mixed** — the categorized stories disagree (two stories calling a
      thread ``financial_pressure`` and two calling it ``membership_movement``
      genuinely has no dominant category);
    * **sparse** — few stories carry a category at all. A thread of one
      ``brokerage_distribution`` story plus one uncategorized story has *zero*
      disagreement; every story that expressed an opinion agreed. Dividing by
      ``len(stories)`` scores that 0.5 and suppresses it, which measured as 6
      of 8 suppressed threads on a realistic window being falsely "mixed" —
      five of them with exactly one category represented. An unlabeled story
      is an absence of evidence, not evidence of conflict.

    Returns ``(dominant, mixed)``:

    * no story is categorized at all -> ``("", False)`` — there is nothing to
      be "mixed" about, just an absence of category signal;
    * some stories are categorized but no category clears the >50% bar ->
      ``("", True)``;
    * a category clears the bar -> ``(that category, False)``.
    """
    weights = {c.key: c.weight for c in config.categories}
    counts: Counter = Counter()
    for s in stories:
        cat = s.get("primary_category") or "uncategorized"
        if cat != "uncategorized":
            counts[cat] += 1
    if not counts:
        return "", False
    top = max(counts, key=lambda k: (counts[k], weights.get(k, 0.0), k))
    if counts[top] / sum(counts.values()) > 0.5:
        return top, False
    return "", True


def _dominant_category(stories: list[dict], config: AppConfig) -> str:
    """Most common real ``primary_category`` in a cluster ("" if all unlabeled
    or if none holds a clear majority — see ``_category_split``)."""
    return _category_split(stories, config)[0]


def _prefer_bigrams(ranked: list[str]) -> list[str]:
    """Promote each bigram ahead of its own constituent unigram, if both survive.

    Ranking by z alone can put a shorter, more-frequent unigram ahead of one
    of its own bigrams (e.g. "ratings" outranking "star ratings") even though
    the bigram is the more specific, more nameable idea. That is a different
    concern from the no-shared-word rule in :func:`_label_candidates` (which
    stops two co-*chosen* terms from repeating a word): here both terms are
    still candidates, and the only question is which one should lead. The
    unigram is not dropped — it stays in the list for a later slot, in case
    the eventual head term doesn't already use its word.

    Returns ``ranked`` with each bigram moved to immediately precede any of
    its constituent unigrams that would otherwise outrank it; everything else
    keeps its relative order (stable), so the result stays deterministic.
    """
    bigrams_by_word: dict[str, list[str]] = {}
    for term in ranked:
        words = term.split()
        if len(words) == 2:
            for word in words:
                bigrams_by_word.setdefault(word, []).append(term)

    ordered: list[str] = []
    placed: set[str] = set()
    for term in ranked:
        if term in placed:
            continue
        if len(term.split()) == 1:
            for bigram in bigrams_by_word.get(term, ()):
                if bigram not in placed:
                    ordered.append(bigram)
                    placed.add(bigram)
        ordered.append(term)
        placed.add(term)
    return ordered


def _label_candidates(
    members: list[int], doc_terms: list[Counter], global_terms: Counter
) -> list[str]:
    """All eligible label terms for a thread, ranked and de-overlapped, uncapped.

    Shared by :func:`_label` (which keeps only the top ``max_terms`` of
    these) and ``build_threads``' label-collision escalation ladder, which
    may need a third term to disambiguate two threads that would otherwise
    land on the same two-term label. Empty when the cluster spans the whole
    window (no background to contrast) or no term survives ranking.
    """
    pos: Counter = Counter()
    for i in members:
        pos.update(doc_terms[i])
    neg = global_terms - pos
    if not neg:  # cluster is the entire window — nothing to contrast against
        return []
    vocab = {w for w in pos if pos[w] + neg[w] >= _LABEL_MIN_DF}
    if len(vocab) < 2:
        return []
    ranked = _prefer_bigrams(
        distinctive_terms(pos, neg, vocab, min_share=_LABEL_MIN_SHARE)
    )
    chosen: list[str] = []
    chosen_tokens: set[str] = set()
    for term in ranked:
        tokens = set(term.split())
        # Skip a term that repeats a word already in the label, so the parts read
        # as distinct ideas rather than "rising medical · medical loss".
        if tokens & chosen_tokens:
            continue
        chosen.append(term)
        chosen_tokens |= tokens
    return chosen


def _label(
    members: list[int],
    doc_terms: list[Counter],
    global_terms: Counter,
    fallback: str,
    *,
    max_terms: int = LABEL_TERMS,
) -> str:
    """Name a thread from the ``max_terms`` terms most distinctive of it.

    Ranks candidates with ``keyword_mining.distinctive_terms`` — the same
    weighted log-odds as :func:`~ma_signal_monitor.keyword_mining._log_odds`
    (Monroe, Colaresi & Quinn 2008), but gated by an in-thread-share floor
    (``_LABEL_MIN_SHARE``) so the ranking measures *distinctiveness*, not
    just *statistical confidence* — confidence grows with raw count, so a
    bare log-odds ranking lets a window-ubiquitous phrase like "medicare
    advantage" beat a thread's own vocabulary just because it's more
    frequent everywhere, not because it says anything about this thread.
    Prefers a bigram over one of its own constituent unigrams for the head
    term (:func:`_prefer_bigrams`) — "star ratings" over bare "ratings" —
    then joins non-overlapping terms so "star ratings · ratings methodology"
    collapses to one idea, not two. Falls back to the dominant taxonomy
    ``fallback`` label when the cluster spans the whole window or surfaces no
    distinctive term. ``max_terms`` beyond the default ``LABEL_TERMS`` is
    used by ``build_threads``' label-collision escalation to earn a thread a
    third term before reaching for other disambiguators.
    """
    chosen = _label_candidates(members, doc_terms, global_terms)[:max_terms]
    return " · ".join(chosen) if chosen else fallback


def _dominant_payer_group(stories: tuple[dict, ...]):
    """Most common canonical payer group among a thread's stories' entities.

    Aliases fold through ``payers.ALIAS_TO_GROUP`` before counting, so e.g.
    "UnitedHealthcare" and "UnitedHealth" mentions on different stories count
    toward the same group rather than splitting the vote (mirroring why
    ``_story_terms`` folds aliases the same way for clustering). Ties break
    toward the group's slug, in the same style as ``_dominant_category``'s
    tie-break, so the choice is deterministic. Returns ``None`` if no story
    entity resolves to a known payer group.
    """
    counts: Counter = Counter()
    for s in stories:
        for alias in s.get("entities") or []:
            group = ALIAS_TO_GROUP.get(alias)
            if group:
                counts[group] += 1
    if not counts:
        return None
    return max(counts, key=lambda g: (counts[g], g.slug))


def _dedupe_labels(
    threads: list[Thread],
    doc_terms: list[Counter],
    global_terms: Counter,
    members_by_key: dict[str, list[int]],
) -> list[Thread]:
    """Make thread labels globally unique, walking ``threads`` in build order.

    ``threads`` arrives in the order ``_cluster`` discovered its groups —
    deterministic (index-based union-find, no hashing or set-iteration
    dependence), not yet sorted for display. Walking in that fixed order and
    escalating any thread whose label collides with one already claimed keeps
    the outcome deterministic too: which of two colliding threads "keeps" the
    plain label never depends on iteration order, only on cluster-discovery
    order.

    Escalation ladder, tried in order, stopping at the first rung that
    produces a label nothing else has claimed yet:

    1. extend the label to a third distinctive term (``LABEL_TERMS + 1``);
    2. append the thread's dominant payer group name (its most common
       entity, folded through ``payers.ALIAS_TO_GROUP``);
    3. append the thread's causal-layer short label;
    4. append a numeric disambiguator — always unique, the ladder's floor.

    Each successful rung's label is claimed immediately, so later threads in
    the walk see it as taken too. Threads whose original label never
    collides are returned untouched.
    """
    used: set[str] = set()
    result: list[Thread] = []
    for t in threads:
        label = t.label
        if label not in used:
            used.add(label)
            result.append(t)
            continue

        # Rung 1: a third distinctive term.
        extended = _label(
            members_by_key[t.key],
            doc_terms,
            global_terms,
            fallback=label,
            max_terms=LABEL_TERMS + 1,
        )
        if extended not in used:
            used.add(extended)
            result.append(replace(t, label=extended))
            continue
        label = extended

        # Rung 2: dominant payer group name.
        group = _dominant_payer_group(t.stories)
        if group is not None:
            candidate = f"{label} · {group.name}"
            if candidate not in used:
                used.add(candidate)
                result.append(replace(t, label=candidate))
                continue
            label = candidate

        # Rung 3: causal-layer short label.
        if t.layer_short:
            candidate = f"{label} · {t.layer_short}"
            if candidate not in used:
                used.add(candidate)
                result.append(replace(t, label=candidate))
                continue
            label = candidate

        # Rung 4: numeric disambiguator -- guaranteed unique, last resort.
        n = 2
        while f"{label} ({n})" in used:
            n += 1
        final = f"{label} ({n})"
        used.add(final)
        result.append(replace(t, label=final))
    return result


def build_threads(
    stories: list[dict],
    config: AppConfig,
    *,
    threshold: float,
    min_stories: int,
) -> tuple[list["Thread"], list[dict]]:
    """Cluster ``stories`` into emergent threads, ordered along the causal cascade.

    Returns ``(threads, ungrouped)``:

    * ``threads`` — clusters of at least ``min_stories``, each named on the fly
      and placed on the causal model, ordered by causal layer
      (upstream → downstream), then by size, then label. Labels are made
      globally unique across the returned threads (see ``_dedupe_labels``)
      before that final ordering is applied.
    * ``ungrouped`` — the leftover stories that formed no thread, so the caller
      can render an honest "ungrouped" row and keep the chart total matching the
      list beneath it.

    Above ``MAX_CLUSTER_INPUT`` stories, only the top by relevance are threaded
    and the remainder join ``ungrouped``. Story dicts are the ``_story_view``
    shape (``title``, ``summary``, ``entities``, ``primary_category``,
    ``relevance_score`` — any may be missing).
    """
    if not stories:
        return [], []

    threaded = stories
    overflow: list[dict] = []
    if len(stories) > MAX_CLUSTER_INPUT:
        ordered = sorted(
            stories, key=lambda s: s.get("relevance_score") or 0.0, reverse=True
        )
        threaded, overflow = ordered[:MAX_CLUSTER_INPUT], ordered[MAX_CLUSTER_INPUT:]

    term_sets = [_story_terms(s) for s in threaded]
    # Labels come from headlines only: titles are the cleanest statement of what
    # a story is about, and skipping summaries avoids their boilerplate ("read
    # more", stock disclaimers) surfacing as a thread's name.
    doc_terms = [Counter(_terms(s.get("title") or "")) for s in threaded]
    global_terms: Counter = Counter()
    for dt in doc_terms:
        global_terms.update(dt)

    lm = layer_map(config)
    groups = _cluster(term_sets, threshold, entity_weight=config.thread_entity_weight)

    threads: list[Thread] = []
    ungrouped: list[dict] = list(overflow)
    members_by_key: dict[str, list[int]] = {}
    for members in groups:
        if len(members) < min_stories:
            ungrouped.extend(threaded[i] for i in members)
            continue
        # Descending relevance, ties broken on item_id -- a total order over
        # content alone, so the anchor (stories[0], see `key` below) never
        # depends on DB fetch order. Plain `-(score or 0.0)` sorts descending
        # without needing `reverse=True` (which would also reverse the
        # item_id tie-break).
        ranked = sorted(
            members,
            key=lambda i: (
                -(threaded[i].get("relevance_score") or 0.0),
                threaded[i].get("item_id") or "",
            ),
        )
        cluster_stories = [threaded[i] for i in ranked]
        dominant, mixed = _category_split(cluster_stories, config)
        layer = lm.get(dominant) if dominant else None
        fallback = (
            get_category_label(dominant, config) if dominant else "General signals"
        )
        # The anchor: this thread's highest-relevance member's stable item_id
        # (stories[0], per the ranking above) -- see Thread.key's docstring
        # for why this, not a positional index or a full-membership hash.
        key = cluster_stories[0].get("item_id") or f"thread-{min(members)}"
        members_by_key[key] = members
        threads.append(
            Thread(
                key=key,
                label=_label(members, doc_terms, global_terms, fallback),
                stories=tuple(cluster_stories),
                dominant_category=dominant,
                mixed=mixed,
                layer_key=layer.key if layer else "",
                layer_short=layer.short if layer else "",
                layer_label=layer.label if layer else "",
                layer_order=layer.order if layer else _NO_LAYER_ORDER,
            )
        )

    # Dedupe labels in the still-build-ordered (deterministic, unsorted) list,
    # before the final display sort re-orders by layer/size/label.
    threads = _dedupe_labels(threads, doc_terms, global_terms, members_by_key)
    threads.sort(key=lambda t: (t.layer_order, -t.total, t.label))
    return threads, ungrouped


# --- Causal cascade: layer bands + "leads to" links between threads ---
#
# The lane already places threads on the causal cascade by row order
# (layer_order), but row order alone never states the cause -> effect claim --
# nothing on the page says "this row leads to that one". These two pure
# functions add that: `thread_bands` groups the already-ordered rows under
# their causal-layer headers, and `build_thread_links` draws at most one
# outgoing "leads to" arrow per thread to the single downstream thread its
# own evidence best supports. Both take `threads` (and, for links, the
# window's edge_map) and return plain data -- no template/route concerns --
# mirroring the rest of this module.


def thread_bands(threads: list[Thread], *, has_ungrouped: bool) -> dict[int, str]:
    """Causal-layer band headers for the /timeline/threads strip.

    Returns ``{row_index: band_label}`` for exactly the row indices where a
    new band begins. ``row_index`` lines up with ``threads``' own order --
    the same order ``_timeline_thread_groups`` feeds ``build_strip`` -- with
    the trailing "Ungrouped signals" row (when ``has_ungrouped``) counted as
    row ``len(threads)``, one past the last thread.

    A band begins wherever ``layer_key`` changes from the previous thread;
    since ``threads`` already sorts by ``layer_order`` (upstream ->
    downstream), this only ever walks forward through the cascade, never
    back. ``layer_key == ""`` -- true for both a ``mixed`` thread (step 4)
    and every unplaced-category thread, and implicitly true of the trailing
    ungrouped row -- always labels its band "Unplaced", and every
    consecutive run of such rows shares ONE band: a mixed thread immediately
    followed by the ungrouped row reads as a single trailing section, not
    two adjacent ones.

    Returns ``{}`` when there is nothing to band (no threads and no
    ungrouped row -- the caller's empty-window branch never renders the
    strip at all, so this is mostly a defensive default).
    """
    bands: dict[int, str] = {}
    prev_key: str | None = None  # sentinel: no real layer_key is ever None
    for i, t in enumerate(threads):
        if t.layer_key != prev_key:
            bands[i] = t.layer_short if t.layer_key else "Unplaced"
            prev_key = t.layer_key
    if has_ungrouped and prev_key != "":
        bands[len(threads)] = "Unplaced"
    return bands


def _parse_event_date(value):
    """``event_date`` (an ISO date/datetime string, or falsy) -> a ``date``, or
    ``None`` when missing/unparseable -- the same tolerance
    ``timeline_layout._bucket_days`` applies to the same field."""
    try:
        return datetime.fromisoformat(value or "").date()
    except (ValueError, TypeError):
        return None


def _median_event_date(thread: Thread):
    """A thread's representative event date, or ``None`` if none parse.

    The middle element of the thread's stories' parseable event dates,
    sorted -- no interpolation for an even count (dates aren't numeric to
    average two of); picking the lower-middle element keeps it a single,
    deterministic, real date rather than a synthesized one. Stories with a
    missing/garbage ``event_date`` are skipped, same tolerance as
    ``timeline_layout._bucket_days``.
    """
    dates = sorted(
        d
        for d in (_parse_event_date(s.get("event_date")) for s in thread.stories)
        if d is not None
    )
    if not dates:
        return None
    return dates[len(dates) // 2]


def _thread_evidence_terms(thread: Thread) -> frozenset[str]:
    """A thread's evidence vocabulary for the "leads to" rule.

    The union of the thread's own label terms (its label is already the
    terms most distinctive of it -- see ``_label``; split on the
    ``" · "`` join so e.g. "star ratings · methodology" contributes both
    "star ratings" and "methodology") and every payer group any of its
    stories mentions, folded to the same opaque ``@slug`` token
    ``_story_terms`` uses (so "UnitedHealthcare" and "UnitedHealth" count as
    the same evidence). Two threads sharing a term here is the
    thread-*specific* evidence ``build_thread_links`` requires on top of a
    bare category-edge match -- see that function's docstring for why a
    category edge alone is not enough.
    """
    terms = {term.strip().lower() for term in thread.label.split(" · ") if term.strip()}
    for s in thread.stories:
        for alias in s.get("entities") or []:
            group = ALIAS_TO_GROUP.get(alias)
            if group:
                terms.add(f"@{group.slug}")
    return frozenset(terms)


def build_thread_links(
    threads: list[Thread], edges: dict[tuple[str, str], object]
) -> dict[str, str]:
    """At most one "leads to" link per thread: ``{source_key: target_key}``.

    A candidate ``a -> b`` link requires all three, evaluated independently
    (none is implied by the others):

    1. **A declared, correctly-directed causal edge.** ``causal.lookup_edge``
       matches ``a``'s and ``b``'s dominant categories in either order (edges
       are downstream-only, so at most one direction is ever declared) --
       but only the edge whose OWN ``source`` equals ``a.dominant_category``
       is accepted. Without that check, a coincidental date ordering could
       pair with the model's edge declared in the opposite direction and
       mislabel it as supporting the reverse claim.
    2. **Temporal precedence.** ``a``'s median ``event_date``
       (:func:`_median_event_date`) must strictly precede ``b``'s --
       a self-contained, intra-window comparison; no second window is
       fetched (see the roadmap's "self-contained (preferred first cut)"
       option). A thread with no parseable date on any story can neither
       source nor receive a link.
    3. **Thread-level evidence.** ``a`` and ``b`` must share at least one
       term in :func:`_thread_evidence_terms` -- a payer both mention, or a
       distinctive label term both share. Without this, the rule collapses
       to "these two categories have a declared edge and happened to sort
       in the right order", which stamps out a connector for nearly every
       adjacent-layer thread pair regardless of whether they are actually
       part of the same story (measured on a 15-row realistic window:
       45 category-only candidate pairs -- a hairball, not a cascade).

    ``mixed`` threads (step 4) and threads with no ``dominant_category``
    never source or receive a link -- there is no single category to place
    an edge against.

    Survivors are ranked by ``edge.weight * overlap`` (``overlap`` = the
    number of shared evidence terms), descending; only the single best
    target per source thread is kept. Ties break on the target's ``key``
    ascending, so the result never depends on ``threads``' iteration order
    (every candidate is scored from the full list regardless of walk
    order) -- see ``tests/test_threads.py::test_link_ordering_is_deterministic``.

    An empty ``edges`` map (no causal model loaded) yields no links at all,
    the same graceful degradation the rest of the causal-aware pages apply.
    """
    links: dict[str, str] = {}
    for a in threads:
        if a.mixed or not a.dominant_category:
            continue
        a_date = _median_event_date(a)
        if a_date is None:
            continue
        a_terms = _thread_evidence_terms(a)

        candidates: list[tuple[float, str]] = []
        for b in threads:
            if b.key == a.key or b.mixed or not b.dominant_category:
                continue
            edge = lookup_edge(edges, a.dominant_category, b.dominant_category)
            if edge is None or edge.source != a.dominant_category:
                continue
            b_date = _median_event_date(b)
            if b_date is None or not (a_date < b_date):
                continue
            overlap = len(a_terms & _thread_evidence_terms(b))
            if overlap == 0:
                continue
            candidates.append((edge.weight * overlap, b.key))

        if candidates:
            _score, target = min(candidates, key=lambda c: (-c[0], c[1]))
            links[a.key] = target
    return links
