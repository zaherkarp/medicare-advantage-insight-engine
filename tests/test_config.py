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

    def test_missing_webhook_url_raises(self, project_root_with_config):
        """ValueError when WEBHOOK_URL is not set in non-test mode."""
        env_path = project_root_with_config / ".env"
        env_path.write_text("WEBHOOK_MODE=ntfy\n")
        # Clear any cached env vars
        os.environ.pop("WEBHOOK_URL", None)
        with pytest.raises(ValueError, match="WEBHOOK_URL"):
            load_config(project_root_with_config)

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
