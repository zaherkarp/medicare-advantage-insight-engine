# Fix plan: scheduled workflows failing on `feedparser.sgmllib` import

Status: executed by this branch. Diagnosis authored 2026-07-29.

## What is failing

Both scheduled GitHub Actions workflows on `main` have failed on every run since
2026-07-28 ~14:35 UTC (they were green earlier that same day):

- **Scheduled Monitor Run** (`.github/workflows/scheduled-monitor.yml`) — the
  `ma-signal-monitor` step exits 1.
- **Deploy Static Site** (`.github/workflows/deploy-pages.yml`) — the
  `Ingest latest signals` step exits 1, so the site never rebuilds/deploys.

Both fail with the identical traceback:

```
File ".../site-packages/feedparser/sgml.py", line 30, in <module>
    import feedparser.sgmllib as sgmllib
ModuleNotFoundError: No module named 'feedparser.sgmllib'
```

The **CI** workflow (`ci.yml`) has not run since 2026-07-24 (last push), but its
test job uses the same install recipe, so the next push/PR would fail the same
way. The `Dockerfile` mirrors the recipe too, so container builds are broken as
well. Lint (`ruff format --check` + `ruff check`) and the full test suite (488
tests) pass locally — this is purely a dependency-install breakage, not a code
bug.

## Root cause

`feedparser` was historically split: the package itself plus `sgmllib3k`, an
sdist-only package that fails to build a wheel under modern pip/setuptools. The
repo worked around that everywhere with a hack:

1. `pip download sgmllib3k --no-binary :all:`, untar, and copy `sgmllib.py`
   into site-packages as a **top-level** `sgmllib` module;
2. `pip install feedparser --no-deps` (unpinned) so pip never tries to build
   `sgmllib3k`.

feedparser **6.0.13** (published to PyPI ~2026-07-28) removed the `sgmllib3k`
dependency in favor of a new `feedparser-sgmllib` wheel, and changed its
internal import from the top-level `import sgmllib` to
`import feedparser.sgmllib`. The unpinned `pip install feedparser --no-deps`
started pulling 6.0.13, whose import the top-level `sgmllib.py` copy no longer
satisfies — hence the sudden, simultaneous breakage of every workflow that uses
the hack. (6.0.12 still required `sgmllib3k`; 6.0.13 is the cutover.)

## Fix

The hack is now obsolete: with feedparser ≥ 6.0.13 every dependency installs
from wheels, so plain `pip install` works everywhere. Remove the workaround at
all five sites and require the fixed feedparser:

1. **`pyproject.toml`** — bump `feedparser>=6.0.10` → `feedparser>=6.0.13`;
   delete the `sgmllib3k>=1.0.0` dependency (feedparser 6.0.13 pulls
   `feedparser-sgmllib` on its own).
2. **`.github/workflows/ci.yml`** (test job) — replace the download/untar/copy
   recipe and the `--no-deps` install chain with
   `pip install -e ".[dev]"` (the `dev` extra already carries pytest,
   pytest-cov, responses, and the web deps the test suite needs).
3. **`.github/workflows/scheduled-monitor.yml`** — replace the recipe with
   `pip install .`.
4. **`.github/workflows/deploy-pages.yml`** — replace the recipe with
   `pip install ".[web]"`.
5. **`Dockerfile`** — drop the sgmllib RUN block and the manual `--no-deps` +
   hand-listed dependency installs; `pip install ".[web]"` resolves everything.
6. **`docs/timeline-threads-roadmap.md`** — update the dev-setup aside that
   recommends the `SETUPTOOLS_USE_DISTUTILS=stdlib` workaround for the
   `sgmllib3k` wheel failure; it no longer applies.

## Verification (must all pass before commit)

- In a **fresh venv**: `pip install -e ".[dev]"` succeeds with no sgmllib hack,
  and `python -c "import feedparser"` works (proves the packaging change on its
  own, independent of the session's site-packages).
- Full test suite in that venv: `python -m pytest` — expect 488 passed.
- `ruff format --check src/ tests/ scripts/` and
  `ruff check src/ tests/ scripts/` stay clean.
- `ma-signal-monitor` console script imports (e.g. `--help` / `-h` exits 0, or
  importing `ma_signal_monitor.main` succeeds in the fresh venv).

## Delivery

Commit the changes (including this plan) to
`claude/fable-bug-fixes-plan-y9qf3w`, push, and open a PR against `main`. The
scheduled workflows only run from `main`, so the real-world confirmation is the
first scheduled run after merge; the CI test job on the PR exercises the same
corrected install path, which is the pre-merge signal.
