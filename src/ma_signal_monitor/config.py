"""Configuration loading and validation for MA Signal Monitor."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class SourceConfig:
    """A single feed source configuration.

    The trailing fields are surfaced on the public Sources directory and the
    State Intelligence section; all are optional for backward compatibility.
    """

    name: str
    type: str
    url: str
    priority: int = 3
    enabled: bool = True
    tags: list[str] = field(default_factory=list)
    state: str = "national"  # USPS code or "national"
    geography: str = ""  # free-text region label, e.g. "Southeast"
    cadence: str = ""  # human ingestion cadence; "" -> use global default
    description: str = ""  # one-line "what this source covers"
    homepage: str = ""  # public landing page (distinct from the feed url)


@dataclass
class CategoryConfig:
    """A single taxonomy category."""

    key: str
    label: str
    description: str
    weight: float
    keywords: list[str]


@dataclass
class ScoringConfig:
    """Scoring tuning parameters."""

    keyword_match_base: float = 0.15
    entity_match_boost: float = 0.20
    source_priority_weight: float = 0.10
    multi_category_boost: float = 0.10
    title_keyword_multiplier: float = 1.5


@dataclass
class AppConfig:
    """Full application configuration."""

    # From .env
    webhook_url: str = ""
    webhook_mode: str = "test"  # "ntfy", "generic", "teams", "test"
    log_level: str = "INFO"
    db_path: str = "data/state.db"
    config_dir: str = "config"
    max_items_per_source: int = 50
    min_relevance_score: float = 0.3
    request_timeout: int = 30
    user_agent: str = "MA-Signal-Monitor/1.0 (Educational/Research)"

    # From YAML configs
    sources: list[SourceConfig] = field(default_factory=list)
    categories: list[CategoryConfig] = field(default_factory=list)
    watched_entities: list[str] = field(default_factory=list)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)

    # Delivery settings from app.yaml
    delivery_max_retries: int = 3
    delivery_retry_backoff_base: int = 2
    delivery_timeout: int = 30
    delivery_batch_size: int = 1

    # Processing settings
    max_item_age_days: int = 7
    max_summary_length: int = 500

    # Storage settings
    seen_item_retention_days: int = 90
    delivery_log_retention_days: int = 30
    story_retention_days: int = 365
    candidate_retention_days: int = 180  # prune dormant discovery candidates

    # Source discovery settings (opt-in; see docs/discovery.md)
    discovery_enabled: bool = False
    discovery_min_story_score: float = 0.3  # only harvest links from stories >= this
    discovery_max_domains_per_run: int = 20  # top-N domains autodiscovered per job
    discovery_interval_hours: int = 24  # cadence of the autodiscovery job
    discovery_recheck_days: int = 14  # don't re-probe a domain more often than this
    discovery_min_times_seen: int = 2  # ignore one-off domains
    discovery_autopromote_score: float = 3.0  # auto-promote at/above this score and…
    discovery_autopromote_min_seen: int = 4  # …this many sightings (hybrid policy)

    # Web / scheduling settings
    ingest_interval_hours: int = 6  # used by the in-process scheduler & cadence label
    web_page_size: int = 25  # stories per page in the web feed

    # Daily Briefing digest settings
    digest_enabled: bool = False  # send the digest email on a daily schedule
    digest_hour: int = 13  # UTC hour to send the daily digest
    digest_lookback_hours: int = 24  # window of stories to include
    digest_max_items: int = 12  # max stories per digest
    digest_min_score: float = 0.3  # minimum relevance score to include
    digest_subject_prefix: str = "MA Daily Briefing"

    # SMTP delivery (stdlib smtplib; optional — digest also renders to the web)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    digest_from: str = ""  # sender address; defaults to smtp_user if blank
    digest_to: str = ""  # comma-separated recipient list
    public_base_url: str = ""  # e.g. https://ma.example.com, for links in emails

    # giscus crowd feedback (public, client-side values — not secrets). When
    # giscus_repo + giscus_repo_id + giscus_category_id are all set, static
    # story pages mount a giscus thread keyed on item_id, and
    # ``ma-signal-feedback ingest-github`` can pull reactions back. The API
    # token for ingest is read from GITHUB_TOKEN at run time, never stored here.
    giscus_repo: str = ""  # "owner/name"
    giscus_repo_id: str = ""
    giscus_category: str = "General"
    giscus_category_id: str = ""
    giscus_theme: str = "light"

    # ntfy action-button feedback (owner channel, weight 1.0). When
    # ntfy_feedback_topic is set, ntfy alerts carry 👍/👎 buttons that publish a
    # vote to that topic; ``ma-signal-feedback ingest-ntfy`` polls it back in.
    ntfy_server: str = "https://ntfy.sh"
    ntfy_feedback_topic: str = ""

    # Source-yield review policy (see docs/feedback.md / source_review.py). A
    # source is flagged for review once it has this many items but a relevance
    # yield below the floor and a best score below the max-score floor.
    source_review_min_sample: int = 25
    source_review_yield_floor: float = 0.05
    source_review_max_score_floor: float = 0.2

    @property
    def giscus_enabled(self) -> bool:
        """True when enough giscus config is present to mount the widget."""
        return bool(
            self.giscus_repo and self.giscus_repo_id and self.giscus_category_id
        )

    @property
    def ntfy_feedback_enabled(self) -> bool:
        """True when ntfy alerts should carry feedback action buttons."""
        return bool(self.ntfy_feedback_topic)


def load_config(project_root: str | Path | None = None) -> AppConfig:
    """Load configuration from .env and YAML files.

    Args:
        project_root: Root directory of the project. Defaults to cwd.

    Returns:
        Populated AppConfig instance.

    Raises:
        FileNotFoundError: If required config files are missing.
        ValueError: If configuration is invalid.
    """
    root = Path(project_root) if project_root else Path.cwd()

    # Load .env
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    config = AppConfig(
        webhook_url=os.getenv("WEBHOOK_URL", ""),
        webhook_mode=os.getenv("WEBHOOK_MODE", "test"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        db_path=os.getenv("DB_PATH", "data/state.db"),
        config_dir=os.getenv("CONFIG_DIR", "config"),
        max_items_per_source=int(os.getenv("MAX_ITEMS_PER_SOURCE", "50")),
        min_relevance_score=float(os.getenv("MIN_RELEVANCE_SCORE", "0.3")),
        request_timeout=int(os.getenv("REQUEST_TIMEOUT", "30")),
        user_agent=os.getenv(
            "USER_AGENT", "MA-Signal-Monitor/1.0 (Educational/Research)"
        ),
        ingest_interval_hours=int(os.getenv("INGEST_INTERVAL_HOURS", "6")),
        web_page_size=int(os.getenv("WEB_PAGE_SIZE", "25")),
        digest_enabled=os.getenv("DIGEST_ENABLED", "false").lower()
        in ("1", "true", "yes"),
        digest_hour=int(os.getenv("DIGEST_HOUR", "13")),
        digest_lookback_hours=int(os.getenv("DIGEST_LOOKBACK_HOURS", "24")),
        digest_max_items=int(os.getenv("DIGEST_MAX_ITEMS", "12")),
        digest_min_score=float(
            os.getenv("DIGEST_MIN_SCORE", os.getenv("MIN_RELEVANCE_SCORE", "0.3"))
        ),
        digest_subject_prefix=os.getenv("DIGEST_SUBJECT_PREFIX", "MA Daily Briefing"),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_user=os.getenv("SMTP_USER", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_use_tls=os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes"),
        digest_from=os.getenv("DIGEST_FROM", ""),
        digest_to=os.getenv("DIGEST_TO", ""),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
        giscus_repo=os.getenv("GISCUS_REPO", ""),
        giscus_repo_id=os.getenv("GISCUS_REPO_ID", ""),
        giscus_category=os.getenv("GISCUS_CATEGORY", "General"),
        giscus_category_id=os.getenv("GISCUS_CATEGORY_ID", ""),
        giscus_theme=os.getenv("GISCUS_THEME", "light"),
        ntfy_server=os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/"),
        ntfy_feedback_topic=os.getenv("NTFY_FEEDBACK_TOPIC", ""),
        source_review_min_sample=int(os.getenv("SOURCE_REVIEW_MIN_SAMPLE", "25")),
        source_review_yield_floor=float(os.getenv("SOURCE_REVIEW_YIELD_FLOOR", "0.05")),
        source_review_max_score_floor=float(
            os.getenv("SOURCE_REVIEW_MAX_SCORE_FLOOR", "0.2")
        ),
        candidate_retention_days=int(os.getenv("CANDIDATE_RETENTION_DAYS", "180")),
        discovery_enabled=os.getenv("DISCOVERY_ENABLED", "false").lower()
        in ("1", "true", "yes"),
        discovery_min_story_score=float(os.getenv("DISCOVERY_MIN_STORY_SCORE", "0.3")),
        discovery_max_domains_per_run=int(
            os.getenv("DISCOVERY_MAX_DOMAINS_PER_RUN", "20")
        ),
        discovery_interval_hours=int(os.getenv("DISCOVERY_INTERVAL_HOURS", "24")),
        discovery_recheck_days=int(os.getenv("DISCOVERY_RECHECK_DAYS", "14")),
        discovery_min_times_seen=int(os.getenv("DISCOVERY_MIN_TIMES_SEEN", "2")),
        discovery_autopromote_score=float(
            os.getenv("DISCOVERY_AUTOPROMOTE_SCORE", "3.0")
        ),
        discovery_autopromote_min_seen=int(
            os.getenv("DISCOVERY_AUTOPROMOTE_MIN_SEEN", "4")
        ),
    )

    config_dir = root / config.config_dir

    # Load sources.yaml
    sources_path = config_dir / "sources.yaml"
    if sources_path.exists():
        config.sources = _load_sources(sources_path)
    else:
        raise FileNotFoundError(f"Sources config not found: {sources_path}")

    # Load taxonomy.yaml
    taxonomy_path = config_dir / "taxonomy.yaml"
    if taxonomy_path.exists():
        _load_taxonomy(taxonomy_path, config)
    else:
        raise FileNotFoundError(f"Taxonomy config not found: {taxonomy_path}")

    # Load app.yaml (optional overrides)
    app_yaml_path = config_dir / "app.yaml"
    if app_yaml_path.exists():
        _load_app_yaml(app_yaml_path, config)

    # Merge any promoted/auto-promoted discovery feeds from the archive DB.
    if config.discovery_enabled:
        _merge_promoted_sources(config, root)

    _validate_config(config)
    return config


def _merge_promoted_sources(config: AppConfig, root: Path) -> None:
    """Append promoted discovery feeds (DB overlay) to the YAML source list.

    Reads ``candidate_sources`` directly so config loading stays independent of
    the storage layer. Guarded so a missing/locked DB degrades to YAML-only and
    never breaks config loading.
    """
    import logging
    import sqlite3

    db_file = root / config.db_path
    if not db_file.exists():
        return
    try:
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT feed_url, domain, feed_title FROM candidate_sources "
                "WHERE status IN ('promoted', 'auto_promoted')"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        logging.getLogger("ma_signal_monitor.config").warning(
            "Could not merge promoted discovery sources: %s", e
        )
        return

    existing = {s.url for s in config.sources}
    for r in rows:
        if r["feed_url"] in existing:
            continue
        config.sources.append(
            SourceConfig(
                name=r["feed_title"] or r["domain"],
                type="rss",
                url=r["feed_url"],
                priority=2,
                enabled=True,
                tags=["discovered"],
                homepage=f"https://{r['domain']}/",
                description="Auto-discovered source promoted from candidates.",
            )
        )
        existing.add(r["feed_url"])


def _load_sources(path: Path) -> list[SourceConfig]:
    """Parse sources.yaml into SourceConfig objects."""
    with open(path) as f:
        data = yaml.safe_load(f)

    sources = []
    for item in data.get("sources", []):
        sources.append(
            SourceConfig(
                name=item["name"],
                type=item["type"],
                url=item["url"],
                priority=item.get("priority", 3),
                enabled=item.get("enabled", True),
                tags=item.get("tags", []),
                state=item.get("state", "national"),
                geography=item.get("geography", ""),
                cadence=item.get("cadence", ""),
                description=item.get("description", ""),
                homepage=item.get("homepage", ""),
            )
        )
    return sources


def _load_taxonomy(path: Path, config: AppConfig) -> None:
    """Parse taxonomy.yaml into config."""
    with open(path) as f:
        data = yaml.safe_load(f)

    categories = []
    for key, cat_data in data.get("categories", {}).items():
        categories.append(
            CategoryConfig(
                key=key,
                label=cat_data["label"],
                description=cat_data["description"],
                weight=cat_data.get("weight", 1.0),
                keywords=cat_data.get("keywords", []),
            )
        )
    config.categories = categories
    config.watched_entities = data.get("watched_entities", [])

    scoring_data = data.get("scoring", {})
    config.scoring = ScoringConfig(
        keyword_match_base=scoring_data.get("keyword_match_base", 0.15),
        entity_match_boost=scoring_data.get("entity_match_boost", 0.20),
        source_priority_weight=scoring_data.get("source_priority_weight", 0.10),
        multi_category_boost=scoring_data.get("multi_category_boost", 0.10),
        title_keyword_multiplier=scoring_data.get("title_keyword_multiplier", 1.5),
    )


def _load_app_yaml(path: Path, config: AppConfig) -> None:
    """Parse app.yaml overrides into config."""
    with open(path) as f:
        data = yaml.safe_load(f)

    delivery = data.get("delivery", {})
    config.delivery_max_retries = delivery.get(
        "max_retries", config.delivery_max_retries
    )
    config.delivery_retry_backoff_base = delivery.get(
        "retry_backoff_base", config.delivery_retry_backoff_base
    )
    config.delivery_timeout = delivery.get("timeout", config.delivery_timeout)
    config.delivery_batch_size = delivery.get("batch_size", config.delivery_batch_size)

    processing = data.get("processing", {})
    config.min_relevance_score = processing.get(
        "min_relevance_score", config.min_relevance_score
    )
    config.max_item_age_days = processing.get(
        "max_item_age_days", config.max_item_age_days
    )
    config.max_summary_length = processing.get(
        "max_summary_length", config.max_summary_length
    )

    storage = data.get("storage", {})
    config.seen_item_retention_days = storage.get(
        "seen_item_retention_days", config.seen_item_retention_days
    )
    config.delivery_log_retention_days = storage.get(
        "delivery_log_retention_days", config.delivery_log_retention_days
    )
    config.story_retention_days = storage.get(
        "story_retention_days", config.story_retention_days
    )


def _validate_config(config: AppConfig) -> None:
    """Validate configuration, raising ValueError on problems."""
    if config.webhook_mode not in ("ntfy", "generic", "teams", "test"):
        raise ValueError(
            f"WEBHOOK_MODE must be 'ntfy', 'generic', 'teams', or 'test', got: {config.webhook_mode}"
        )

    if config.webhook_mode != "test" and not config.webhook_url:
        import logging

        logging.getLogger("ma_signal_monitor.config").warning(
            "WEBHOOK_URL is not set but WEBHOOK_MODE='%s'. "
            "Falling back to WEBHOOK_MODE='test' (dry-run).",
            config.webhook_mode,
        )
        config.webhook_mode = "test"

    enabled_sources = [s for s in config.sources if s.enabled]
    if not enabled_sources:
        raise ValueError("No enabled sources found in sources.yaml")

    if not config.categories:
        raise ValueError("No taxonomy categories found in taxonomy.yaml")

    if not 0.0 <= config.min_relevance_score <= 1.0:
        raise ValueError(
            f"min_relevance_score must be between 0.0 and 1.0, got: {config.min_relevance_score}"
        )
