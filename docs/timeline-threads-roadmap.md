# Timeline emergent story-threads — phased roadmap

Handoff doc for continuing the `/timeline/threads` work in a later session.
Phase 1 ships in **PR #56** (`claude/timeline-aggregator-categorization-upmsh4`).

## Context

The timeline's topic strip groups a window's stories into the six **fixed**
taxonomy rows. This workstream adds an alternate lane that groups them into
**emergent, on-the-fly "threads"** instead: cluster the window's stories, name
each thread from its own distinctive language, and place it on the declared
causal model (`config/causal_model.yaml`) — so the lane reads as the real
stories of the window flowing down the cause → effect cascade.

**Hard guardrail (do not break):** deterministic, no ML, no embeddings, no
network, no new runtime dependencies. All three steps reuse in-repo primitives.
JS-free and static-export-safe (the whole timeline is absolutely-positioned
HTML so it survives the GitHub Pages build).

## Status at a glance

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Emergent thread lane (cluster → label → causal placement), `/timeline/threads` | ✅ Done — PR #56 |
| — | CI ruff-drift fix (pin lint rule set) | ✅ Done — PR #56 (`pyproject.toml`) |
| 2 | Causal cause→effect connectors between threads | ⬜ Not started |
| 3 | Enhancements backlog (callouts, continuity, scoped threads, perf) | ⬜ Backlog |

---

## Phase 1 — Emergent thread lane ✅ (PR #56)

Delivered. Reference for what exists so Phase 2+ can build on it.

**New modules**
- `src/ma_signal_monitor/threads.py` — the engine. Public API:
  `build_threads(stories, config, *, threshold, min_stories) -> (list[Thread], ungrouped)`.
  - `Thread` dataclass: `key, label, stories, dominant_category, layer_key,
    layer_short, layer_label, layer_order` + `.total`.
  - `_cluster` — title+entity token-Jaccard single-linkage via a shared-term
    inverted index with rare-term blocking (`_DF_BLOCK_FRACTION`), `MAX_CLUSTER_INPUT`
    safety cap.
  - `_label` — top distinctive terms via `keyword_mining._log_odds` over
    headlines; falls back to the dominant taxonomy label.
  - `_dominant_category`, `_story_terms`.
- `src/ma_signal_monitor/causal.py` — shared causal-graph helpers extracted from
  `angles.py`: `edge_map`, `lookup_edge`, `layer_map`, `layers_for_topics`.
  `angles.py` imports them under their old private names, so its behavior/tests
  are unchanged.

**Wiring**
- `web/routes.py` — `_timeline_thread_groups(...)` (emits the same
  `(key, label, href, color, stories)` tuples `build_strip` already consumes,
  plus a trailing "Ungrouped signals" row); `_render_timeline(..., threads=True)`;
  `/timeline/threads` route (404s when disabled); `view_toggle`; `thread_legend`.
- `timeline_layout.build_strip` renders the lane **unchanged** — it was already
  generic over groups.
- `web/templates/timeline.html` (toggle + legend include), `_thread_legend.html`,
  CSS (`.timeline-views`, `.legend-layer`).
- `static_export.py` — freezes `/timeline/threads` at the default window.
- Config: `config/app.yaml` → `timeline.threads` (`enabled`,
  `similarity_threshold: 0.28`, `min_stories: 2`); loaded + validated in `config.py`
  (`threads_enabled`, `thread_similarity_threshold`, `thread_min_stories`).

**Tests:** `tests/test_threads.py`, `tests/test_causal.py`, threads cases added
to `tests/test_web.py`, threads-page assertion in `tests/test_static_export.py`.

---

## Phase 2 — Causal cause→effect connectors ⬜

**Goal.** Turn the vertical cascade of thread rows into an explicit graph: draw a
JS-free connector from thread A to thread B when A's dominant category and B's
dominant category lie on a declared causal edge **and** A temporally precedes B
(a cause must come before its effect).

**Design (deterministic, JS-free, static-safe)**
1. **Which pairs connect.** For each ordered thread pair, use
   `causal.lookup_edge(edge_map, a.dominant_category, b.dominant_category)`; keep
   the pair only if a downstream edge exists (edges are downstream-only, so the
   direction is unambiguous). Threads already sort by `layer_order`, so real
   connectors mostly point downward — short, clean hops.
2. **Temporal precedence.** Two options:
   - *Self-contained (preferred first cut):* compare intra-window timing — draw
     A→B only if A's stories predominantly precede B's rise (e.g. A's median/
     earliest `event_date` < B's). No extra query.
   - *Full parity with Angles:* also fetch the previous window and reuse the
     `_sequence_consistent` predicate at thread level (upstream present last
     window, downstream rising now). If you go here, move `_sequence_consistent`
     (and its `_momentum` helper) from `angles.py` into `causal.py` and import it
     in both, the same way Phase 1 shared the graph helpers.
3. **Geometry.** Add `build_thread_connectors(strip_rows/threads, edge_map, ...)`
   to `timeline_layout.py` returning per-connector positions: source/target row
   indices and an x-anchor (reuse `cell_pct(peak_day_index, days)`). Return a
   frozen dataclass list like the other layout outputs; keep the module pure (no
   color literals — pass colors in like `build_strip`/`build_callout_band` do).
4. **Render.** Overlay one inline-SVG line layer (or absolutely-positioned thin
   divs) on the `.topic-strip`, consistent with the existing percentage-x / pixel-y
   convention. CSS in `web/static/style.css`. No JavaScript.
5. **Config.** Add `thread_connectors_enabled` (default true) under
   `timeline.threads`; degrade to no connectors when the causal model is empty.

**Files:** `timeline_layout.py` (geometry), `web/routes.py` (pass connectors into
the context), `web/templates/timeline.html` + `style.css` (render), `threads.py`
or `causal.py` (precedence helper), `config.py`/`app.yaml` (flag).

**Verify:** unit-test connector geometry (deterministic), on-edge-only,
sequence-consistent-only, and empty-model → no connectors; web test that the
threads page renders a connector between a Drivers thread and a Pressure thread;
`ruff` + full `pytest`; render + static-export smoke.

---

## Phase 3 — Enhancements backlog ⬜

Pick as needed; each is independent.

- **Callout-per-thread.** Change `build_callout_band` selection from one winner
  per *day* to one per *thread* so labels track real threads. Touches
  `timeline_layout.py` + `tests/test_timeline_layout.py` (shared with all timeline
  pages — guard behind the threads path or verify no regression).
- **Thread continuity across windows.** Track a thread over time ("day 3 of X")
  by matching this window's threads to last window's via label/story overlap;
  surfaces momentum per thread.
- **Scoped threads.** Allow the threads lane under a topic/payer/state filter
  (cluster the scoped stories). Today the lane is unscoped (root only).
- **Tuning.** Auto-calibrate `thread_similarity_threshold`; expose a per-window
  override; revisit `MAX_CLUSTER_INPUT` behavior (currently top-by-relevance,
  remainder → ungrouped — already surfaced, not silently dropped).
- **Performance hardening.** Stress-test clustering near `TIMELINE_MAX_STORIES`
  (5000); confirm rare-term blocking keeps it bounded; add a micro-benchmark.
- **Multi-category threads.** Consider labeling/placing a thread that genuinely
  spans two layers (today it takes the single dominant category).
- **(Guardrail-gated) LLM labeling.** Only if the owner ever relaxes the no-LLM
  guardrail: an optional, config-gated LLM naming/merging step, off by default,
  falling back to the current log-odds labels. Out of scope unless explicitly
  approved.

---

## Run / verify (any phase)

```bash
pip install -e ".[dev,web]"        # if sgmllib3k wheel fails: prefix SETUPTOOLS_USE_DISTUTILS=stdlib
python scripts/seed_test_data.py   # populate the stories archive
uvicorn ma_signal_monitor.web.app:app_factory --factory --port 8000
# visit /timeline/threads  and  /timeline  (Topics ↔ Threads toggle)

python -m pytest -q                # full suite
ruff format --check src/ tests/ scripts/ && ruff check src/ tests/ scripts/
ma-signal-build                    # static export; confirm timeline/threads.html, no JS
```

**CI note.** `ruff` is installed unpinned in CI; the lint rule set is pinned in
`pyproject.toml` (`[tool.ruff.lint] select = ["E4","E7","E9","F"]`) so a ruff
version bump can't newly flag the existing codebase. Keep that in place.

## Key reuse map

- Clustering similarity → `similarity.jaccard` / `title_terms`
- Cluster labeling → `keyword_mining._terms` / `_log_odds`
- Causal graph access → `causal.edge_map` / `lookup_edge` / `layer_map` / `layers_for_topics`
- Temporal precedence (Phase 2) → `angles._sequence_consistent` (relocate to `causal.py` if shared)
- Strip rendering → `timeline_layout.build_strip` (generic over `(key, label, href, color, stories)`)
- Colors → `topic_colors.topic_color` / `topic_color_map` / `FALLBACK_TOPIC_COLOR`
