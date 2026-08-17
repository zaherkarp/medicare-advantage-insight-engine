"""FastAPI application factory for the MA Signal Monitor web frontend.

The same SQLite file written by the ingestion pipeline (``main.run``) is read
here to serve a browsable archive. WAL mode (enabled in ``StateStore``) lets the
web reader and the scheduled writer share the file without blocking.
"""

import logging
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ma_signal_monitor.classify import get_category_label
from ma_signal_monitor.config import AppConfig, load_config
from ma_signal_monitor.geo import state_name
from ma_signal_monitor.storage import StateStore
from ma_signal_monitor.topic_colors import topic_color, topic_color_map
from ma_signal_monitor.web.routes import register_routes

logger = logging.getLogger("ma_signal_monitor.web")

_WEB_DIR = Path(__file__).parent


def create_app(
    config: AppConfig,
    store: StateStore,
    *,
    enable_scheduler: bool = False,
    project_root: Path | None = None,
    static_export: bool = False,
) -> FastAPI:
    """Build the FastAPI app.

    Args:
        config: Loaded application configuration.
        store: A StateStore opened against the archive DB.
        enable_scheduler: If True, run ingestion on an interval in-process.
        project_root: Root used by the scheduled ingestion run.
        static_export: If True, templates render for the static GitHub Pages
            build — interactive controls that POST back (e.g. the feedback
            widget) are swapped for their static equivalents (giscus).
    """
    app = FastAPI(title="MA Signal Monitor", docs_url=None, redoc_url=None)
    app.state.config = config
    # Apply the MA-eligibility display floor (hide tier 'exclude') to every
    # public read this store serves, when the flag is on. Default-off leaves the
    # store byte-identical. See StateStore.eligibility_gate.
    store.eligibility_gate = config.ma_eligibility_gate
    app.state.store = store

    templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    # Helpers available to every template.
    templates.env.globals["category_label"] = lambda key: get_category_label(
        key, config
    )
    # Resolved once (positional palette + YAML overrides) and reused for every
    # lookup — see topic_colors.py for the fallback semantics.
    color_map = topic_color_map(config.categories)
    templates.env.globals["topic_color"] = lambda key: topic_color(key, color_map)
    templates.env.globals["state_name"] = state_name
    templates.env.globals["categories"] = config.categories
    templates.env.globals["static_site"] = static_export
    templates.env.globals["giscus"] = {
        "enabled": config.giscus_enabled,
        "repo": config.giscus_repo,
        "repo_id": config.giscus_repo_id,
        "category": config.giscus_category,
        "category_id": config.giscus_category_id,
        "theme": config.giscus_theme,
    }
    app.state.templates = templates

    app.mount(
        "/static",
        StaticFiles(directory=str(_WEB_DIR / "static")),
        name="static",
    )

    register_routes(app, templates)

    if enable_scheduler:
        _setup_scheduler(app, config, project_root or Path.cwd())

    return app


def _setup_scheduler(app: FastAPI, config: AppConfig, project_root: Path) -> None:
    """Run the ingestion pipeline on an interval inside the web process."""
    from apscheduler.schedulers.background import BackgroundScheduler

    from ma_signal_monitor.main import run as run_pipeline

    lock = threading.Lock()

    def _ingest() -> None:
        if not lock.acquire(blocking=False):
            logger.info("Ingestion already running; skipping this tick")
            return
        try:
            summary = run_pipeline(config=config, project_root=project_root)
            logger.info("Scheduled ingestion complete: %s", summary)
        except Exception as e:  # never let the scheduler thread die
            logger.exception("Scheduled ingestion failed: %s", e)
        finally:
            lock.release()

    def _daily_digest() -> None:
        from ma_signal_monitor.digest import generate_digest

        try:
            generate_digest(config, app.state.store, send=True)
        except Exception as e:
            logger.exception("Daily digest generation failed: %s", e)

    def _discovery() -> None:
        from ma_signal_monitor.discovery.runner import run_discovery

        try:
            run_discovery(config, app.state.store)
        except Exception as e:
            logger.exception("Scheduled discovery failed: %s", e)

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        _ingest,
        "interval",
        hours=config.ingest_interval_hours,
        id="ingest",
    )
    if config.digest_enabled:
        scheduler.add_job(
            _daily_digest,
            "cron",
            hour=config.digest_hour,
            minute=0,
            id="daily_digest",
        )
    if config.discovery_enabled:
        scheduler.add_job(
            _discovery,
            "interval",
            hours=config.discovery_interval_hours,
            id="discovery",
        )

    @app.on_event("startup")
    def _start() -> None:
        # Backfill once on boot if the archive is empty, then start the loop.
        if app.state.store.count_stories() == 0:
            threading.Thread(target=_ingest, daemon=True).start()
        scheduler.start()
        logger.info(
            "Scheduler started (ingest every %dh%s)",
            config.ingest_interval_hours,
            f", digest daily at {config.digest_hour:02d}:00 UTC"
            if config.digest_enabled
            else "",
        )

    @app.on_event("shutdown")
    def _stop() -> None:
        scheduler.shutdown(wait=False)


def app_factory() -> FastAPI:
    """Build the app from the current working directory config.

    Used by ``uvicorn ma_signal_monitor.web.app:app_factory --factory``. The
    scheduler is enabled via the RUN_SCHEDULER env var (set in the Docker image).
    Importing this module stays cheap and requires no config on disk — the
    config is only read when the factory is invoked.
    """
    import os

    root = Path.cwd()
    config = load_config(root)
    store = StateStore(root / config.db_path)
    enable = os.getenv("RUN_SCHEDULER", "false").lower() in ("1", "true", "yes")
    return create_app(config, store, enable_scheduler=enable, project_root=root)
