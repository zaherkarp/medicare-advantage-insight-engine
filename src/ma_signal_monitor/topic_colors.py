"""Per-topic color assignment for the /timeline page (callouts, bubbles, legend).

Categories carry no color by default. A color is assigned *positionally*
from :data:`DEFAULT_TOPIC_PALETTE`, keyed to the order categories appear in
``config/taxonomy.yaml`` (``AppConfig.categories`` — see
``config._load_taxonomy``), which is stable/append-only: a fresh taxonomy
edit never reshuffles an existing category's color, it only extends the
list. An operator can pin a specific category's color by setting a
``color:`` hex string in taxonomy.yaml (validated in
``config._validate_config``); that override always wins over the positional
default. Categories beyond the palette's length, "uncategorized", "Other"
grouping rows, and any unrecognized key all resolve to
:data:`FALLBACK_TOPIC_COLOR` — the site's neutral ``--muted`` grey — so they
read as "no assigned identity" rather than colliding with a real topic's hue.

The six-hue palette was chosen and validated (dataviz palette validator) for
adjacent-pair color-vision-deficiency safety against the site's white
``--card`` background (see the plan's D4 design decision).

Like :mod:`ma_signal_monitor.trends` and :mod:`ma_signal_monitor.lanes`, this
module does no I/O: :func:`topic_color_map` is resolved once per app/build
from the loaded config, and :func:`topic_color` is a cheap per-key lookup
against that resolved map thereafter.
"""

from ma_signal_monitor.config import CategoryConfig

# Categorical palette, assigned positionally in taxonomy config order (see
# module docstring). Adjacent pairs are CVD-validated against --card:#ffffff.
DEFAULT_TOPIC_PALETTE: tuple[str, ...] = (
    "#2a78d6",
    "#008300",
    "#e87ba4",
    "#eda100",
    "#1baf7a",
    "#eb6834",
)

# The site's --muted grey (web/static/style.css :root). Used for
# "uncategorized", "Other" grouping rows, categories beyond the palette's
# length, and unknown keys — deliberately not a series hue.
FALLBACK_TOPIC_COLOR = "#687385"


def topic_color_map(categories: list[CategoryConfig]) -> dict[str, str]:
    """Resolve each category's color: YAML ``color`` override, else the
    positional :data:`DEFAULT_TOPIC_PALETTE` slot for its index, else
    :data:`FALLBACK_TOPIC_COLOR` once the palette runs out.

    ``categories`` is expected in taxonomy config order (how
    ``config._load_taxonomy`` builds ``AppConfig.categories``). Call once per
    app/build and reuse the result with :func:`topic_color`.
    """
    color_map: dict[str, str] = {}
    for index, cat in enumerate(categories):
        if cat.color:
            color_map[cat.key] = cat.color
        elif index < len(DEFAULT_TOPIC_PALETTE):
            color_map[cat.key] = DEFAULT_TOPIC_PALETTE[index]
        else:
            color_map[cat.key] = FALLBACK_TOPIC_COLOR
    return color_map


def topic_color(key: str | None, color_map: dict[str, str]) -> str:
    """Look up ``key``'s color in ``color_map``, built by :func:`topic_color_map`.

    Falls back to :data:`FALLBACK_TOPIC_COLOR` for ``None``/empty keys and
    for any key absent from the map (e.g. "uncategorized", "Other", or a
    category removed from the taxonomy since the map was built).
    """
    if not key:
        return FALLBACK_TOPIC_COLOR
    return color_map.get(key, FALLBACK_TOPIC_COLOR)
