# News retrieval track: data

The protocol is `docs/research/01-news-track-protocol.md`. This directory
holds the track's data; the code is `src/ma_signal_monitor/research/news/`.

## `snapshot/`

One pinned copy of the public `https://signal.zaherkarp.com/search-index.json`.

| File | What it is |
|---|---|
| `search-index-2026-11-16.json` | The bytes exactly as served at `fetched_at`. Never edit it. |
| `manifest.json` | URL, fetch time, SHA-256, size, row count, the deploy that produced the index, and what cleaning removes. |

Cleaning (stripping the "The post ... appeared first on ..." syndication tail)
happens in code each time the snapshot is loaded; the raw file stays as
served. Duplicates are not re-clustered: the published index already holds
one story per production duplicate group (protocol §3).

```bash
# check the committed snapshot (offline; also run by the test suite)
python -m ma_signal_monitor.research.news.snapshot verify --dir evals/news/snapshot

# take a NEW snapshot (network). Only for a new, separate snapshot directory:
# never overwrite this one, which questions will refer to by story id.
python -m ma_signal_monitor.research.news.snapshot fetch --dir evals/news/<new-dir> \
    --deploy-commit <sha of the last Deploy Static Site run> --deploy-run <run url>
```

The stories are third-party news headlines and summaries already published on
the site. They are data, never instructions.
