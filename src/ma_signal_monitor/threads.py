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

* **Cluster** — title + entity token-Jaccard single-linkage, the same
  :func:`ma_signal_monitor.similarity.jaccard` the near-duplicate grouper uses,
  at a *looser* threshold (a thread is broader than a near-duplicate). Candidate
  pairs come from a shared-term inverted index with rare-term blocking, so the
  pairwise work stays well below the naive all-pairs cost.
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

from ma_signal_monitor.causal import layer_map
from ma_signal_monitor.classify import get_category_label
from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.keyword_mining import _terms, distinctive_terms
from ma_signal_monitor.payers import ALIAS_TO_GROUP
from ma_signal_monitor.similarity import jaccard, title_terms

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

    ``label`` is the thread's on-the-fly name — its most distinctive terms, or
    the dominant taxonomy label when the cluster is too small/uniform to name.
    The ``layer_*`` fields place the thread on the declared causal model via its
    dominant category; ``layer_order`` drives the upstream → downstream row order
    (``_NO_LAYER_ORDER`` when the thread can't be placed).
    """

    key: str
    label: str
    stories: tuple[dict, ...]
    dominant_category: str
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


def _cluster(term_sets: list[set[str]], threshold: float) -> list[list[int]]:
    """Single-linkage clusters of story indices by token-Jaccard ≥ ``threshold``.

    Candidate pairs come from a shared-term inverted index with rare-term
    blocking (ubiquitous terms are skipped as blocking keys), so the pairwise
    Jaccard work stays bounded well below the naive all-pairs cost. Returns index
    groups (each ordered ascending); every input index lands in exactly one group,
    singletons included. Union-find connectivity makes the result independent of
    term/iteration order, so clustering is deterministic across runs.
    """
    n = len(term_sets)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path halving
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # smaller index stays root

    df: Counter = Counter()
    for ts in term_sets:
        df.update(ts)
    df_cap = max(3, int(_DF_BLOCK_FRACTION * n))

    index: dict[str, list[int]] = {}
    for i, ts in enumerate(term_sets):
        for t in ts:
            if df[t] <= df_cap:
                index.setdefault(t, []).append(i)

    checked: set[tuple[int, int]] = set()
    for members in index.values():
        for a_pos in range(len(members)):
            i = members[a_pos]
            for b_pos in range(a_pos + 1, len(members)):
                j = members[b_pos]  # i < j: postings appended in ascending i
                if find(i) == find(j) or (i, j) in checked:
                    continue
                checked.add((i, j))
                if jaccard(term_sets[i], term_sets[j]) >= threshold:
                    union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _dominant_category(stories: list[dict], config: AppConfig) -> str:
    """Most common real ``primary_category`` in a cluster ("" if all unlabeled).

    Ties break toward the higher taxonomy weight (mirroring ``classify_item``),
    then the key, so the choice is deterministic.
    """
    weights = {c.key: c.weight for c in config.categories}
    counts: Counter = Counter()
    for s in stories:
        cat = s.get("primary_category") or "uncategorized"
        if cat != "uncategorized":
            counts[cat] += 1
    if not counts:
        return ""
    return max(counts, key=lambda k: (counts[k], weights.get(k, 0.0), k))


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
    groups = _cluster(term_sets, threshold)

    threads: list[Thread] = []
    ungrouped: list[dict] = list(overflow)
    members_by_key: dict[str, list[int]] = {}
    for members in groups:
        if len(members) < min_stories:
            ungrouped.extend(threaded[i] for i in members)
            continue
        ranked = sorted(
            members,
            key=lambda i: threaded[i].get("relevance_score") or 0.0,
            reverse=True,
        )
        cluster_stories = [threaded[i] for i in ranked]
        dominant = _dominant_category(cluster_stories, config)
        layer = lm.get(dominant) if dominant else None
        fallback = (
            get_category_label(dominant, config) if dominant else "General signals"
        )
        key = f"thread-{min(members)}"
        members_by_key[key] = members
        threads.append(
            Thread(
                key=key,
                label=_label(members, doc_terms, global_terms, fallback),
                stories=tuple(cluster_stories),
                dominant_category=dominant,
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
