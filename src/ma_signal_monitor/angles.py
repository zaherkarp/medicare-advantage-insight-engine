"""Angles: ways of looking at the week's signals through lens intersections.

Reframes a recent story window as a venn-diagram of analytical lenses. Every
story already sits on several lenses (its full ``categories`` list, the
canonical payers behind its ``entities``, its ``states``); a card forms where
two of those lenses *overlap* — payer × topic, topic × topic, topic × state,
payer × payer — so the page surfaces the intersections a plain per-topic list
threw away.

Overlaps are then weighted by a declared causal layer model (see
``config/causal_model.yaml``): the six taxonomy categories are stages in a
cascade (policy/demographic drivers → financial pressure → strategic response →
enrollment outcomes), and an intersection lying *along* a declared causal edge
outranks an incidental one. A payer active on both ends of an edge this window
is a two-step cascade. Ranking is transparent arithmetic —
``rank_score = count × (1 + boost × edge_weight)`` — never a learned model.

Card text is derived from the facts in the window (counts, sources, momentum,
the strongest headline); there are no borrowed hooks or hashtags. Pure
data-in/data-out so it is unit-testable without HTTP: the ``/angles`` route
feeds it the web layer's facet dicts for the current and previous windows.
"""

from itertools import combinations

from ma_signal_monitor.classify import get_category_label
from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.geo import state_name
from ma_signal_monitor.payers import ALIAS_TO_GROUP, get_group

# How many intersection cards the page shows, and how many stories each links.
MAX_ANGLES = 8
STORIES_PER_ANGLE = 3
# An overlap needs at least this many stories to be worth a card — a single
# co-occurrence is noise, not an angle.
MIN_ANGLE_STORIES = 2
# Below this many surviving intersections the window is too thin to read as a
# venn; fall back to single-lens topic cards so the page still says something.
MIN_INTERSECTION_CARDS = 3
# Chips in the window-wide highlights row.
TAGS_PER_HIGHLIGHTS = 5

# Payer-lens kinds. A watched "person" (a named executive) folds to a payer
# page but never anchors a payer intersection — an angle is about organizations.
PAYER_KINDS = frozenset({"payer", "distribution"})

# Momentum ordering for the sort tiebreak: a surging overlap beats a fading one
# at equal rank.
_MOMENTUM_RANK = {"new": 3, "up": 2, "steady": 1, "down": 0}

# Ranking boosts (see module docstring). A cascade (a payer carrying a signal
# across an edge) is the strongest read, then a bare topic chain; both stay < 1
# so a differential edge weight re-ranks without steamrolling raw volume.
CHAIN_BOOST = 0.5
CASCADE_BOOST = 0.75

# The four lens overlaps the engine buckets. Ordered ``(type, lens A, lens B,
# same-lens?)``: same-lens pairs collapse mirrors via a sorted 2-combination,
# cross-lens pairs keep a fixed A/B role (payer before topic, topic before
# state) so keys are canonical.
ANGLE_TYPES = ("payer_topic", "topic_topic", "topic_state", "payer_payer")
_PAIR_SPECS = (
    ("payer_topic", "payers", "topics", False),
    ("topic_topic", "topics", "topics", True),
    ("topic_state", "topics", "states", False),
    ("payer_payer", "payers", "payers", True),
)
_SPEC_LENSES = {typ: (lens_a, lens_b) for typ, lens_a, lens_b, _ in _PAIR_SPECS}

_TYPE_LABELS = {
    "payer_topic": "Payer × Topic",
    "topic_topic": "Topic × Topic",
    "causal_chain": "Causal chain",
    "topic_state": "Topic × State",
    "payer_payer": "Payer × Payer",
    "payer_cascade": "Payer cascade",
    "topic": "Topic",
}


def _fold_payers(stories: list[dict]) -> list[dict]:
    """Count canonical payer groups mentioned across ``stories``.

    Each story counts a group once even when several of its aliases match.
    Aliases without a canonical group (e.g. agencies like CMS) are skipped —
    the chips link to payer pages, which only exist for grouped organizations.
    """
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for s in stories:
        seen: set[str] = set()
        for alias in s.get("entities") or []:
            group = ALIAS_TO_GROUP.get(alias)
            if group is None or group.slug in seen:
                continue
            seen.add(group.slug)
            counts[group.slug] = counts.get(group.slug, 0) + 1
            names[group.slug] = group.name
    return [
        {"slug": slug, "name": names[slug], "count": n}
        for slug, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _fold_states(stories: list[dict]) -> list[dict]:
    """Count state codes mentioned across ``stories`` (once per story)."""
    counts: dict[str, int] = {}
    for s in stories:
        for code in set(s.get("states") or []):
            counts[code] = counts.get(code, 0) + 1
    return [
        {"code": code, "count": n}
        for code, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _momentum(count: int, prev_count: int) -> str:
    """Label this window's volume against the previous window's."""
    if prev_count == 0:
        return "new"
    if count > prev_count:
        return "up"
    if count < prev_count:
        return "down"
    return "steady"


def _story_lenses(story: dict, config: AppConfig) -> dict[str, list[str]]:
    """Pull the analytical lenses a single story sits on.

    Topics are the story's full category list — the multi-category matches the
    old primary-category grouping discarded — de-duplicated and stripped of
    ``uncategorized``; a story archived before multi-category storage falls back
    to its ``primary_category`` so old rows still carry a topic. Payers are the
    canonical groups its entities fold into, filtered to ``PAYER_KINDS`` so a
    named person never anchors a payer intersection. States are de-duplicated
    as they are.
    """
    topics: list[str] = []
    seen_t: set[str] = set()
    for cat in story.get("categories") or []:
        if cat and cat != "uncategorized" and cat not in seen_t:
            seen_t.add(cat)
            topics.append(cat)
    if not topics:
        primary = story.get("primary_category")
        if primary and primary != "uncategorized":
            topics.append(primary)

    payers: list[str] = []
    seen_p: set[str] = set()
    for alias in story.get("entities") or []:
        group = ALIAS_TO_GROUP.get(alias)
        if group is None or group.kind not in PAYER_KINDS or group.slug in seen_p:
            continue
        seen_p.add(group.slug)
        payers.append(group.slug)

    states: list[str] = []
    seen_s: set[str] = set()
    for code in story.get("states") or []:
        if code and code not in seen_s:
            seen_s.add(code)
            states.append(code)

    return {"topics": topics, "payers": payers, "states": states}


def _bucket_pairs(stories: list[dict], config: AppConfig) -> dict[tuple, list[dict]]:
    """Group stories into ``(type, a, b)`` intersection buckets.

    One pass over the window: each story drops a copy into every pair its lenses
    form. A same-lens pair is keyed by the sorted 2-combination so ``(A, B)``
    and ``(B, A)`` never split into mirror buckets; a cross-lens pair keeps a
    fixed A/B role order. A story lands in a given bucket at most once, so
    bucket length is a true story count.
    """
    buckets: dict[tuple, list[dict]] = {}
    for s in stories:
        lenses = _story_lenses(s, config)
        for typ, lens_a, lens_b, same in _PAIR_SPECS:
            if same:
                for a, b in combinations(sorted(lenses[lens_a]), 2):
                    buckets.setdefault((typ, a, b), []).append(s)
            else:
                for a in lenses[lens_a]:
                    for b in lenses[lens_b]:
                        buckets.setdefault((typ, a, b), []).append(s)
    return buckets


def _edge_map(config: AppConfig) -> dict[tuple[str, str], object]:
    """Directed ``(source, target) -> edge`` lookup for the causal model."""
    return {(e.source, e.target): e for e in config.causal_edges}


def _lookup_edge(edge_map: dict, a: str, b: str):
    """Return the edge joining topics ``a`` and ``b`` in either order, else None.

    Edges are downstream-only, so at most one of ``(a, b)`` / ``(b, a)`` can be
    declared — the pair is unambiguous however the canonical bucket key sorted
    it.
    """
    return edge_map.get((a, b)) or edge_map.get((b, a))


def _layer_map(config: AppConfig) -> dict[str, object]:
    """``category_key -> layer`` lookup (each category sits in one layer)."""
    return {cat: layer for layer in config.causal_layers for cat in layer.categories}


def _layers_for_topics(topic_keys: list[str], layer_map: dict) -> list[dict]:
    """Distinct layers the topics span, in causal (upstream→downstream) order."""
    seen: dict[str, object] = {}
    for key in topic_keys:
        layer = layer_map.get(key)
        if layer is not None and layer.key not in seen:
            seen[layer.key] = layer
    ordered = sorted(seen.values(), key=lambda ly: ly.order)
    return [{"key": ly.key, "label": ly.label, "short": ly.short} for ly in ordered]


def _side(lens: str, value: str, config: AppConfig) -> dict:
    """Render one side of an intersection as a ``{label, href}`` link.

    A topic whose key is no longer in the taxonomy (a story archived under a
    since-removed category) renders as its bare key with no link, mirroring how
    the feed refuses to mint a dead ``/topics`` URL.
    """
    if lens == "payers":
        group = get_group(value)
        return {"label": group.name if group else value, "href": f"/payers/{value}"}
    if lens == "states":
        return {"label": state_name(value), "href": f"/states/{value}"}
    valid = {c.key for c in config.categories}
    return {
        "label": get_category_label(value, config),
        "href": f"/topics/{value}" if value in valid else None,
    }


def _fact_line(
    count: int, sources: int, momentum: str, prev_count: int, top_title: str
) -> str:
    """A one-line factual summary of an overlap, straight from the window.

    No borrowed draft copy: just the volume, its spread across sources, the
    momentum against last period, and the strongest headline behind the card.
    """
    sig = "signal" if count == 1 else "signals"
    src = "source" if sources == 1 else "sources"
    if momentum == "new":
        trend = "first showing this period"
    elif momentum == "up":
        trend = f"up from {prev_count} last period"
    elif momentum == "down":
        trend = f"down from {prev_count} last period"
    else:
        trend = "steady vs. last period"
    return f"{count} {sig} from {sources} {src}, {trend}. Strongest: “{top_title}”."


def _topic_membership_counts(stories: list[dict], config: AppConfig) -> dict[str, int]:
    """Fold topic-lens membership: ``{topic_key: stories carrying it}``.

    Uses the same topic lens as the buckets so the causal-sequence check reads
    consistent counts (a multi-category story counts under each of its topics).
    """
    counts: dict[str, int] = {}
    for s in stories:
        for topic in _story_lenses(s, config)["topics"]:
            counts[topic] = counts.get(topic, 0) + 1
    return counts


def _sequence_consistent(
    edge,
    curr_topics: dict[str, int],
    prev_topics: dict[str, int],
    previous_empty: bool,
) -> bool | None:
    """Is the edge's temporal story borne out this window vs. last?

    The model predicts an upstream cause precedes a downstream effect, so a
    chain is "sequence consistent" when the source was already present last
    period and the target is rising now. ``None`` (unknowable) when there is no
    previous window to compare against.
    """
    if previous_empty:
        return None
    return prev_topics.get(edge.source, 0) > 0 and _momentum(
        curr_topics.get(edge.target, 0), prev_topics.get(edge.target, 0)
    ) in ("new", "up")


def _derive_cascades(buckets: dict[tuple, list[dict]], config: AppConfig) -> dict:
    """Two-step payer cascades: a payer active on both ends of a causal edge.

    For every declared edge A→B, a payer with stories in both its ``payer × A``
    and ``payer × B`` buckets this window is carrying the signal along that
    edge. The card's stories are the item-id-deduped union of the two buckets
    (a story matching both A and B counts once), so a cascade never inflates its
    own volume. Reuses the already-built ``payer_topic`` buckets — no extra pass
    over the window.
    """
    by_payer: dict[str, dict[str, list[dict]]] = {}
    for (typ, a, b), stories in buckets.items():
        if typ == "payer_topic":
            by_payer.setdefault(a, {})[b] = stories
    cascades: dict[tuple, dict] = {}
    for edge in config.causal_edges:
        for payer, topics in by_payer.items():
            src = topics.get(edge.source)
            tgt = topics.get(edge.target)
            if not src or not tgt:
                continue
            union: list[dict] = []
            seen: set[str] = set()
            for s in (*src, *tgt):
                if s["item_id"] not in seen:
                    seen.add(s["item_id"])
                    union.append(s)
            cascades[(payer, edge.source, edge.target)] = {
                "stories": union,
                "edge": edge,
            }
    return cascades


def _base_card(stories: list[dict], prev_count: int) -> dict:
    """Common facts shared by every card type, from its story set."""
    ranked = sorted(
        stories, key=lambda s: s.get("relevance_score") or 0.0, reverse=True
    )
    count = len(ranked)
    top = ranked[0]
    return {
        "ranked": ranked,
        "count": count,
        "prev_count": prev_count,
        "momentum": _momentum(count, prev_count),
        "sources": len({s["source_name"] for s in ranked}),
        "top_score": top.get("relevance_score") or 0.0,
        "top_title": top["title"],
        "item_ids": frozenset(s["item_id"] for s in ranked),
    }


def _causal_block(
    edge, config: AppConfig, curr_topics: dict, prev_topics: dict, previous_empty: bool
) -> dict:
    """The per-card causal annotation: direction, weight, evidence, sequence."""
    return {
        "source": edge.source,
        "target": edge.target,
        "source_label": get_category_label(edge.source, config),
        "target_label": get_category_label(edge.target, config),
        "weight": edge.weight,
        "evidence": edge.evidence,
        "sequence_consistent": _sequence_consistent(
            edge, curr_topics, prev_topics, previous_empty
        ),
    }


def _assemble(
    card_type: str,
    sides: list[dict],
    label: str,
    base: dict,
    causal: dict | None,
    layers: list[dict],
    rank_score: float,
    *,
    fallback: bool,
) -> dict:
    """Compose the template-ready card dict from its parts."""
    return {
        "type": card_type,
        "type_label": _TYPE_LABELS[card_type],
        "label": label,
        "sides": sides,
        "count": base["count"],
        "prev_count": base["prev_count"],
        "momentum": base["momentum"],
        "top_score": base["top_score"],
        "sources": base["sources"],
        "stories": base["ranked"][:STORIES_PER_ANGLE],
        "fact_line": _fact_line(
            base["count"],
            base["sources"],
            base["momentum"],
            base["prev_count"],
            base["top_title"],
        ),
        "fallback": fallback,
        "rank_score": rank_score,
        "layers": layers,
        "causal": causal,
        # Suppression bookkeeping; stripped before the view-model is returned.
        "item_ids": base["item_ids"],
    }


def _pair_card(
    typ: str,
    a: str,
    b: str,
    stories: list[dict],
    prev_count: int,
    edge,
    config: AppConfig,
    layer_map: dict,
    curr_topics: dict,
    prev_topics: dict,
    previous_empty: bool,
) -> dict:
    """Build a card from a lens-pair bucket, promoting edge overlaps to chains."""
    base = _base_card(stories, prev_count)
    if edge is not None:
        # A declared edge lifts a plain topic∩topic overlap into a directional
        # chain: its sides and label follow the edge (source → target), not the
        # canonical bucket sort, but the caller keeps the canonical key so the
        # previous-window count still matches.
        sides = [
            _side("topics", edge.source, config),
            _side("topics", edge.target, config),
        ]
        label = f"{sides[0]['label']} → {sides[1]['label']}"
        return _assemble(
            "causal_chain",
            sides,
            label,
            base,
            _causal_block(edge, config, curr_topics, prev_topics, previous_empty),
            _layers_for_topics([edge.source, edge.target], layer_map),
            base["count"] * (1 + CHAIN_BOOST * edge.weight),
            fallback=False,
        )
    lens_a, lens_b = _SPEC_LENSES[typ]
    sides = [_side(lens_a, a, config), _side(lens_b, b, config)]
    topic_keys = [v for lens, v in ((lens_a, a), (lens_b, b)) if lens == "topics"]
    return _assemble(
        typ,
        sides,
        f"{sides[0]['label']} ∩ {sides[1]['label']}",
        base,
        None,
        _layers_for_topics(topic_keys, layer_map),
        float(base["count"]),
        fallback=False,
    )


def _cascade_card(
    payer: str,
    edge,
    stories: list[dict],
    prev_count: int,
    config: AppConfig,
    layer_map: dict,
    curr_topics: dict,
    prev_topics: dict,
    previous_empty: bool,
) -> dict:
    """Build a two-step cascade card: ``Payer: source → target``."""
    base = _base_card(stories, prev_count)
    payer_side = _side("payers", payer, config)
    src_side = _side("topics", edge.source, config)
    tgt_side = _side("topics", edge.target, config)
    return _assemble(
        "payer_cascade",
        [payer_side, src_side, tgt_side],
        f"{payer_side['label']}: {src_side['label']} → {tgt_side['label']}",
        base,
        _causal_block(edge, config, curr_topics, prev_topics, previous_empty),
        _layers_for_topics([edge.source, edge.target], layer_map),
        base["count"] * (1 + CASCADE_BOOST * edge.weight),
        fallback=False,
    )


def _topic_fallback_cards(
    current: list[dict], previous: list[dict], config: AppConfig, layer_map: dict
) -> list[dict]:
    """Single-lens topic cards for when intersections are too sparse.

    Falls back to the old primary-category grouping so a thin window still reads
    as something; ranked by raw volume (boost 0) and flagged ``fallback`` so the
    template files them under "More angles". payer × payer and state overlaps
    are intentionally excluded — the fallback is a topic-level reading of the
    week, not a second pass at the venn.
    """
    prev_counts: dict[str, int] = {}
    for s in previous:
        key = s.get("primary_category") or "uncategorized"
        if key != "uncategorized":
            prev_counts[key] = prev_counts.get(key, 0) + 1
    by_topic: dict[str, list[dict]] = {}
    for s in current:
        key = s.get("primary_category") or "uncategorized"
        if key == "uncategorized":
            continue
        by_topic.setdefault(key, []).append(s)
    cards = []
    for key, stories in by_topic.items():
        base = _base_card(stories, prev_counts.get(key, 0))
        side = _side("topics", key, config)
        cards.append(
            _assemble(
                "topic",
                [side],
                side["label"],
                base,
                None,
                _layers_for_topics([key], layer_map),
                float(base["count"]),
                fallback=True,
            )
        )
    return cards


def _sort_key(card: dict) -> tuple:
    """Global ordering: rank, then surging over fading, then score, then label."""
    return (
        -card["rank_score"],
        -_MOMENTUM_RANK[card["momentum"]],
        -card["top_score"],
        card["label"],
    )


def _suppress_subsets(cards: list[dict]) -> list[dict]:
    """Greedy subset suppression over pre-sorted cards.

    Walk the ranked cards; drop any whose story set is a subset of an
    already-accepted card's set. A dropped card can't suppress a later one (only
    accepted cards do), and a cascade that outranks its own P×A / P×B
    constituents absorbs them here for free.
    """
    kept: list[dict] = []
    for card in cards:
        ids = card["item_ids"]
        if any(ids <= k["item_ids"] for k in kept):
            continue
        kept.append(card)
    return kept


def _causal_model_view(config: AppConfig) -> dict | None:
    """About-panel shape for the declared causal model, or None when unset.

    Powers the JS-free "About this model" panel: the ordered layers and the
    downstream edges with their weights and citable evidence, exactly as
    declared in ``config/causal_model.yaml`` — inspectable, not inferred.
    """
    if not config.causal_model_enabled:
        return None
    layers = [
        {
            "key": ly.key,
            "label": ly.label,
            "short": ly.short,
            "categories": [
                {"key": c, "label": get_category_label(c, config)}
                for c in ly.categories
            ],
        }
        for ly in sorted(config.causal_layers, key=lambda ly: ly.order)
    ]
    edges = [
        {
            "source_label": get_category_label(e.source, config),
            "target_label": get_category_label(e.target, config),
            "weight": e.weight,
            "evidence": e.evidence,
        }
        for e in config.causal_edges
    ]
    return {"layers": layers, "edges": edges}


def build_angles(current: list[dict], previous: list[dict], config: AppConfig) -> dict:
    """Build the Angles view-model from two adjacent story windows.

    ``current`` and ``previous`` are facet dicts for the last-N-days window and
    the N days before it. Returns ``{"angles": [...], "highlights": {...}}``:
    one globally ranked list of intersection cards (the template partitions it
    into causal chains vs. the rest for display). Cards are ranked by
    ``rank_score = count × (1 + boost × edge_weight)``, momentum-broken ties,
    then de-duplicated by greedy subset suppression and capped. When too few
    intersections survive, single-lens topic cards are appended so a thin window
    still reads. With an empty causal model the whole thing degrades to plain
    intersections (no chains, cascades, layers, or About panel).

    Worked ranks (equal-volume overlaps re-order by edge weight; a boost < 1
    never overtakes a much larger plain overlap):
        plain 6-story          = 6.0
        cascade w=1.0 3-story   = 3 × (1 + 0.75 × 1.0) = 5.25
        chain  w=1.0 2-story    = 2 × (1 + 0.50 × 1.0) = 3.0
        chain  w=0.6 2-story    = 2 × (1 + 0.50 × 0.6) = 2.6
        plain 2-story           = 2.0
    """
    edge_map = _edge_map(config)
    layer_map = _layer_map(config)
    previous_empty = not previous
    curr_topics = _topic_membership_counts(current, config)
    prev_topics = _topic_membership_counts(previous, config)

    curr_buckets = _bucket_pairs(current, config)
    prev_buckets = _bucket_pairs(previous, config)
    curr_cascades = _derive_cascades(curr_buckets, config)
    prev_cascades = _derive_cascades(prev_buckets, config)

    cards: list[dict] = []
    for (typ, a, b), stories in curr_buckets.items():
        if len(stories) < MIN_ANGLE_STORIES:
            continue
        prev_count = len(prev_buckets.get((typ, a, b), ()))
        edge = _lookup_edge(edge_map, a, b) if typ == "topic_topic" else None
        cards.append(
            _pair_card(
                typ,
                a,
                b,
                stories,
                prev_count,
                edge,
                config,
                layer_map,
                curr_topics,
                prev_topics,
                previous_empty,
            )
        )
    for (payer, source, target), data in curr_cascades.items():
        stories = data["stories"]
        if len(stories) < MIN_ANGLE_STORIES:
            continue
        prev_count = len(
            prev_cascades.get((payer, source, target), {}).get("stories", ())
        )
        cards.append(
            _cascade_card(
                payer,
                data["edge"],
                stories,
                prev_count,
                config,
                layer_map,
                curr_topics,
                prev_topics,
                previous_empty,
            )
        )

    cards.sort(key=_sort_key)
    kept = _suppress_subsets(cards)[:MAX_ANGLES]

    # Sparse window: enrich with single-lens topic cards (ranked + suppressed
    # among themselves) so the page isn't nearly empty. They sit below the
    # surviving intersections, which stay the more specific read.
    if len(kept) < MIN_INTERSECTION_CARDS:
        fallback = _topic_fallback_cards(current, previous, config, layer_map)
        fallback.sort(key=_sort_key)
        kept.extend(_suppress_subsets(fallback))

    for card in kept:
        card.pop("item_ids", None)

    return {
        "angles": kept,
        "highlights": {
            "total": len(current),
            "payers": _fold_payers(current)[:TAGS_PER_HIGHLIGHTS],
            "states": _fold_states(current)[:TAGS_PER_HIGHLIGHTS],
        },
    }
