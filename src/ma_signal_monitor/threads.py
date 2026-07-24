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
* **Label** — the terms most over-represented in a thread versus the rest of the
  window, via the keyword-mining n-gram + weighted log-odds machinery
  (:func:`ma_signal_monitor.keyword_mining._terms` / ``_log_odds``).
* **Place** — the thread's dominant taxonomy category mapped onto the declared
  causal layers through :mod:`ma_signal_monitor.causal`.

Pure data-in/data-out so it is unit-testable without HTTP, mirroring
:mod:`ma_signal_monitor.angles`.
"""

from collections import Counter
from dataclasses import dataclass

from ma_signal_monitor.causal import layer_map
from ma_signal_monitor.classify import get_category_label
from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.keyword_mining import _log_odds, _terms
from ma_signal_monitor.similarity import jaccard, title_terms

# A thread label shows at most this many distinctive terms (joined by " · ").
LABEL_TERMS = 2
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
    """Clustering token set: headline content words plus canonical entity tokens.

    Title tokens carry the topic; entity aliases (lowercased) bind stories about
    the same organization even when the wording differs. Taxonomy categories are
    left out on purpose — they are the *coarse* grouping this lane exists to go
    beneath, so letting them merge would just rebuild the topic rows.
    """
    terms = set(title_terms(story.get("title") or ""))
    for alias in story.get("entities") or []:
        if alias:
            terms.add(alias.lower())
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


def _label(
    members: list[int],
    doc_terms: list[Counter],
    global_terms: Counter,
    fallback: str,
) -> str:
    """Name a thread from the terms most over-represented in it vs. the window.

    Reuses the keyword-mining weighted log-odds (Monroe, Colaresi & Quinn 2008)
    with the rest of the window as the background pool, preferring longer phrases,
    then joins the top ``LABEL_TERMS`` distinctive terms that share no word — so
    "star ratings · ratings methodology" collapses to one idea, not two
    overlapping ones. Falls back to the dominant taxonomy ``fallback`` label when
    the cluster spans the whole window (no background to contrast) or surfaces no
    distinctive term.
    """
    pos: Counter = Counter()
    for i in members:
        pos.update(doc_terms[i])
    neg = global_terms - pos
    if not neg:  # cluster is the entire window — nothing to contrast against
        return fallback
    vocab = {w for w in pos if pos[w] + neg[w] >= _LABEL_MIN_DF}
    if len(vocab) < 2:
        return fallback
    z = _log_odds(pos, neg, vocab)
    ranked = sorted(
        (w for w in vocab if z[w] > 0),
        key=lambda w: (-z[w], -len(w), w),
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
        if len(chosen) >= LABEL_TERMS:
            break
    return " · ".join(chosen) if chosen else fallback


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
      (upstream → downstream), then by size, then label.
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
        threads.append(
            Thread(
                key=f"thread-{min(members)}",
                label=_label(members, doc_terms, global_terms, fallback),
                stories=tuple(cluster_stories),
                dominant_category=dominant,
                layer_key=layer.key if layer else "",
                layer_short=layer.short if layer else "",
                layer_label=layer.label if layer else "",
                layer_order=layer.order if layer else _NO_LAYER_ORDER,
            )
        )

    threads.sort(key=lambda t: (t.layer_order, -t.total, t.label))
    return threads, ungrouped
