"""Tests for configuration loading and validation."""

import os

import pytest
import yaml

from ma_signal_monitor.config import load_config

# Env vars that load_config may set via dotenv
_ENV_KEYS = [
    "WEBHOOK_URL",
    "WEBHOOK_MODE",
    "LOG_LEVEL",
    "DB_PATH",
    "CONFIG_DIR",
    "MAX_ITEMS_PER_SOURCE",
    "MIN_RELEVANCE_SCORE",
    "ARCHIVE_MIN_SCORE",
    "REQUEST_TIMEOUT",
    "USER_AGENT",
]


@pytest.fixture(autouse=True)
def _clean_env():
    """Remove dotenv-set env vars before and after each test."""
    for key in _ENV_KEYS:
        os.environ.pop(key, None)
    yield
    for key in _ENV_KEYS:
        os.environ.pop(key, None)


class TestConfigLoading:
    """Test config loading from files."""

    def test_loads_valid_config(self, project_root_with_config):
        """Config loads successfully from valid files."""
        config = load_config(project_root_with_config)
        assert config.webhook_url == "https://webhook.site/test-uuid"
        assert config.webhook_mode == "test"
        assert len(config.sources) == 1
        assert config.sources[0].name == "Test Feed"
        assert len(config.categories) >= 1

    def test_loads_sources(self, project_root_with_config):
        """Sources are loaded with correct attributes."""
        config = load_config(project_root_with_config)
        source = config.sources[0]
        assert source.type == "rss"
        assert source.url == "https://example.com/feed"
        assert source.priority == 4
        assert source.enabled is True

    def test_loads_taxonomy(self, project_root_with_config):
        """Taxonomy categories and entities are loaded."""
        config = load_config(project_root_with_config)
        assert len(config.categories) >= 1
        cat = config.categories[0]
        assert cat.key == "membership_movement"
        assert "enrollment" in cat.keywords
        assert "UnitedHealthcare" in config.watched_entities

    def test_loads_scoring_config(self, project_root_with_config):
        """Scoring parameters are loaded from taxonomy."""
        config = load_config(project_root_with_config)
        assert config.scoring.keyword_match_base == 0.15
        assert config.scoring.entity_match_boost == 0.20

    def test_ma_context_gate_defaults(self, project_root_with_config):
        """MA-context gate defaults are sane when the taxonomy omits them."""
        config = load_config(project_root_with_config)
        assert config.scoring.ma_context_min_priority == 3
        assert config.ma_context_terms == []

    def test_archive_min_score_defaults(self, project_root_with_config):
        """archive_min_score defaults to the noise floor when unset."""
        config = load_config(project_root_with_config)
        assert config.archive_min_score == 0.1

    def test_archive_min_score_env_override(self, project_root_with_config):
        """ARCHIVE_MIN_SCORE overrides the default (0 disables filtering)."""
        (project_root_with_config / ".env").write_text(
            "WEBHOOK_URL=https://test.com\nWEBHOOK_MODE=test\nARCHIVE_MIN_SCORE=0\n"
        )
        os.environ.pop("ARCHIVE_MIN_SCORE", None)
        config = load_config(project_root_with_config)
        assert config.archive_min_score == 0.0

    def test_thread_entity_weight_defaults(self, project_root_with_config):
        """thread_entity_weight defaults to 1.0 when app.yaml doesn't set it.

        The knob is unused until step 3 wires it into the clusterer's weighted
        Jaccard, but it must still load and validate like any other setting.
        """
        config = load_config(project_root_with_config)
        assert config.thread_entity_weight == 1.0

    def test_thread_max_rows_defaults(self, project_root_with_config):
        """thread_max_rows defaults to 25 when app.yaml doesn't set it."""
        config = load_config(project_root_with_config)
        assert config.thread_max_rows == 25

    def test_thread_max_rows_loads_from_app_yaml(self, project_root_with_config):
        app_yaml_path = project_root_with_config / "config" / "app.yaml"
        app_yaml_path.write_text(yaml.dump({"timeline": {"threads": {"max_rows": 10}}}))
        config = load_config(project_root_with_config)
        assert config.thread_max_rows == 10

    def test_missing_sources_file_raises(self, tmp_path):
        """FileNotFoundError when sources.yaml is missing."""
        (tmp_path / "config").mkdir()
        (tmp_path / ".env").write_text("WEBHOOK_URL=https://test.example.com\n")
        with pytest.raises(FileNotFoundError, match="sources.yaml"):
            load_config(tmp_path)

    def test_missing_taxonomy_file_raises(self, tmp_path):
        """FileNotFoundError when taxonomy.yaml is missing."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        sources = {
            "sources": [
                {"name": "X", "type": "rss", "url": "https://x.com", "enabled": True}
            ]
        }
        with open(config_dir / "sources.yaml", "w") as f:
            yaml.dump(sources, f)
        (tmp_path / ".env").write_text("WEBHOOK_URL=https://test.example.com\n")
        with pytest.raises(FileNotFoundError, match="taxonomy.yaml"):
            load_config(tmp_path)


class TestConfigValidation:
    """Test configuration validation rules."""

    def test_missing_webhook_url_falls_back_to_test(self, project_root_with_config):
        """Falls back to test mode when WEBHOOK_URL is not set in non-test mode."""
        env_path = project_root_with_config / ".env"
        env_path.write_text("WEBHOOK_MODE=ntfy\n")
        # Clear any cached env vars
        os.environ.pop("WEBHOOK_URL", None)
        config = load_config(project_root_with_config)
        assert config.webhook_mode == "test"

    def test_missing_webhook_url_ok_in_test_mode(self, project_root_with_config):
        """No error when WEBHOOK_URL is missing in test mode."""
        env_path = project_root_with_config / ".env"
        env_path.write_text("WEBHOOK_MODE=test\n")
        os.environ.pop("WEBHOOK_URL", None)
        config = load_config(project_root_with_config)
        assert config.webhook_mode == "test"
        assert config.webhook_url == ""

    def test_invalid_webhook_mode_raises(self, project_root_with_config):
        """ValueError for invalid WEBHOOK_MODE."""
        env_path = project_root_with_config / ".env"
        env_path.write_text("WEBHOOK_URL=https://test.com\nWEBHOOK_MODE=invalid\n")
        os.environ.pop("WEBHOOK_MODE", None)
        with pytest.raises(ValueError, match="WEBHOOK_MODE"):
            load_config(project_root_with_config)

    def test_no_enabled_sources_raises(self, project_root_with_config):
        """ValueError when all sources are disabled."""
        config_dir = project_root_with_config / "config"
        sources = {
            "sources": [
                {"name": "X", "type": "rss", "url": "https://x.com", "enabled": False}
            ]
        }
        with open(config_dir / "sources.yaml", "w") as f:
            yaml.dump(sources, f)
        with pytest.raises(ValueError, match="No enabled sources"):
            load_config(project_root_with_config)

    def test_invalid_relevance_score_raises(self, project_root_with_config):
        """ValueError for out-of-range min_relevance_score."""
        env_path = project_root_with_config / ".env"
        env_path.write_text(
            "WEBHOOK_URL=https://test.com\nWEBHOOK_MODE=test\nMIN_RELEVANCE_SCORE=2.0\n"
        )
        os.environ.pop("MIN_RELEVANCE_SCORE", None)
        with pytest.raises(ValueError, match="min_relevance_score"):
            load_config(project_root_with_config)

    def test_invalid_archive_min_score_raises(self, project_root_with_config):
        """ValueError for out-of-range archive_min_score."""
        (project_root_with_config / ".env").write_text(
            "WEBHOOK_URL=https://test.com\nWEBHOOK_MODE=test\nARCHIVE_MIN_SCORE=1.5\n"
        )
        os.environ.pop("ARCHIVE_MIN_SCORE", None)
        with pytest.raises(ValueError, match="archive_min_score"):
            load_config(project_root_with_config)

    def test_invalid_thread_entity_weight_raises(self, project_root_with_config):
        """ValueError for out-of-range timeline.threads.entity_weight."""
        app_yaml_path = project_root_with_config / "config" / "app.yaml"
        app_yaml_path.write_text(
            yaml.dump({"timeline": {"threads": {"entity_weight": 1.5}}})
        )
        with pytest.raises(ValueError, match="thread_entity_weight"):
            load_config(project_root_with_config)

    def test_invalid_thread_max_rows_raises(self, project_root_with_config):
        """ValueError for timeline.threads.max_rows < 1 (0 is not "no cap")."""
        app_yaml_path = project_root_with_config / "config" / "app.yaml"
        app_yaml_path.write_text(yaml.dump({"timeline": {"threads": {"max_rows": 0}}}))
        with pytest.raises(ValueError, match="thread_max_rows"):
            load_config(project_root_with_config)


def test_taxonomy_category_color_round_trips(tmp_path):
    """A category's `color` is parsed onto CategoryConfig; omitted -> ""."""
    from ma_signal_monitor.config import AppConfig, _load_taxonomy

    path = tmp_path / "taxonomy.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "categories": {
                    "membership_movement": {
                        "label": "Membership Movement",
                        "description": "Enrollment changes",
                        "weight": 1.0,
                        "keywords": ["enrollment"],
                        "color": "#2a78d6",
                    },
                    "policy_regulatory": {
                        "label": "Policy / Regulatory Changes",
                        "description": "CMS rules",
                        "weight": 1.2,
                        "keywords": ["CMS"],
                    },
                }
            }
        )
    )
    config = AppConfig()
    _load_taxonomy(path, config)
    by_key = {c.key: c for c in config.categories}
    assert by_key["membership_movement"].color == "#2a78d6"
    assert by_key["policy_regulatory"].color == ""  # omitted -> default


@pytest.mark.parametrize("bad_color", ["red", "#12345", "#gggggg", "2a78d6"])
def test_invalid_category_color_raises(project_root_with_config, bad_color):
    """ValueError when a category's `color` isn't a 6-digit hex value."""
    config_dir = project_root_with_config / "config"
    taxonomy = {
        "categories": {
            "membership_movement": {
                "label": "Membership Movement",
                "description": "Enrollment changes",
                "weight": 1.0,
                "keywords": ["enrollment"],
                "color": bad_color,
            },
        },
        "watched_entities": ["UnitedHealthcare"],
    }
    with open(config_dir / "taxonomy.yaml", "w") as f:
        yaml.dump(taxonomy, f)
    with pytest.raises(ValueError, match="color"):
        load_config(project_root_with_config)


def test_source_context_round_trips(tmp_path):
    """A source's `context` field is parsed from sources.yaml."""
    import yaml

    from ma_signal_monitor.config import _load_sources

    path = tmp_path / "sources.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "name": "Litigation Feed",
                        "type": "litigation",
                        "url": "https://example.com/feed/",
                        "priority": 4,
                        "context": "Medicare Advantage Star Ratings litigation.",
                    },
                    {
                        "name": "Plain Feed",
                        "type": "rss",
                        "url": "https://example.com/rss/",
                    },
                ]
            }
        )
    )
    sources = _load_sources(path)
    assert sources[0].context == "Medicare Advantage Star Ratings litigation."
    assert sources[1].context == ""  # default when omitted
