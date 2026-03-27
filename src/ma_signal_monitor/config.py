"""Configuration loading and validation for MA Signal Monitor."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class SourceConfig:
    """A single feed source configuration."""

    name: str
    type: str
    url: str
    priority: int = 3
    enabled: bool = True
    tags: list[str] = field(default_factory=list)


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
        user_agent=os.getenv("USER_AGENT", "MA-Signal-Monitor/1.0 (Educational/Research)"),
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

    _validate_config(config)
    return config


def _load_sources(path: Path) -> list[SourceConfig]:
    """Parse sources.yaml into SourceConfig objects."""
    with open(path) as f:
        data = yaml.safe_load(f)

    sources = []
    for item in data.get("sources", []):
        sources.append(SourceConfig(
            name=item["name"],
            type=item["type"],
            url=item["url"],
            priority=item.get("priority", 3),
            enabled=item.get("enabled", True),
            tags=item.get("tags", []),
        ))
    return sources


def _load_taxonomy(path: Path, config: AppConfig) -> None:
    """Parse taxonomy.yaml into config."""
    with open(path) as f:
        data = yaml.safe_load(f)

    categories = []
    for key, cat_data in data.get("categories", {}).items():
        categories.append(CategoryConfig(
            key=key,
            label=cat_data["label"],
            description=cat_data["description"],
            weight=cat_data.get("weight", 1.0),
            keywords=cat_data.get("keywords", []),
        ))
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
    config.delivery_max_retries = delivery.get("max_retries", config.delivery_max_retries)
    config.delivery_retry_backoff_base = delivery.get("retry_backoff_base", config.delivery_retry_backoff_base)
    config.delivery_timeout = delivery.get("timeout", config.delivery_timeout)
    config.delivery_batch_size = delivery.get("batch_size", config.delivery_batch_size)

    processing = data.get("processing", {})
    config.min_relevance_score = processing.get("min_relevance_score", config.min_relevance_score)
    config.max_item_age_days = processing.get("max_item_age_days", config.max_item_age_days)
    config.max_summary_length = processing.get("max_summary_length", config.max_summary_length)

    storage = data.get("storage", {})
    config.seen_item_retention_days = storage.get("seen_item_retention_days", config.seen_item_retention_days)
    config.delivery_log_retention_days = storage.get("delivery_log_retention_days", config.delivery_log_retention_days)


def _validate_config(config: AppConfig) -> None:
    """Validate configuration, raising ValueError on problems."""
    if not config.webhook_url:
        raise ValueError(
            "WEBHOOK_URL is not set. Set it in .env or as an environment variable. "
            "For testing, use a Webhook.site URL."
        )

    if config.webhook_mode not in ("ntfy", "generic", "teams", "test"):
        raise ValueError(
            f"WEBHOOK_MODE must be 'ntfy', 'generic', 'teams', or 'test', got: {config.webhook_mode}"
        )

    enabled_sources = [s for s in config.sources if s.enabled]
    if not enabled_sources:
        raise ValueError("No enabled sources found in sources.yaml")

    if not config.categories:
        raise ValueError("No taxonomy categories found in taxonomy.yaml")

    if not 0.0 <= config.min_relevance_score <= 1.0:
        raise ValueError(
            f"min_relevance_score must be between 0.0 and 1.0, got: {config.min_relevance_score}"
        )
