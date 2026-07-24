"""Shared access to the declared causal model (config/causal_model.yaml).

Pure lookups over the declared, evidence-cited causal-layer model — directed,
downstream-only edges between taxonomy categories, grouped into ordered layers.
Both the Angles engine (:mod:`ma_signal_monitor.angles`) and the timeline's
emergent story-thread lane (:mod:`ma_signal_monitor.threads`) read the model
through these helpers, so the graph-access logic lives in exactly one place.

Nothing here trains, fits, or infers anything; it only reads what
config/causal_model.yaml declares (and config.py validated at load time).
"""

from ma_signal_monitor.config import AppConfig


def edge_map(config: AppConfig) -> dict[tuple[str, str], object]:
    """Directed ``(source, target) -> edge`` lookup for the causal model."""
    return {(e.source, e.target): e for e in config.causal_edges}


def lookup_edge(edges: dict, a: str, b: str):
    """Return the edge joining topics ``a`` and ``b`` in either order, else None.

    Edges are downstream-only, so at most one of ``(a, b)`` / ``(b, a)`` can be
    declared — the pair is unambiguous however a canonical bucket key sorted it.
    """
    return edges.get((a, b)) or edges.get((b, a))


def layer_map(config: AppConfig) -> dict[str, object]:
    """``category_key -> layer`` lookup (each category sits in one layer)."""
    return {cat: layer for layer in config.causal_layers for cat in layer.categories}


def layers_for_topics(topic_keys: list[str], layers: dict) -> list[dict]:
    """Distinct layers the topics span, in causal (upstream→downstream) order."""
    seen: dict[str, object] = {}
    for key in topic_keys:
        layer = layers.get(key)
        if layer is not None and layer.key not in seen:
            seen[layer.key] = layer
    ordered = sorted(seen.values(), key=lambda ly: ly.order)
    return [{"key": ly.key, "label": ly.label, "short": ly.short} for ly in ordered]
