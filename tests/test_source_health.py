"""Tests for silent-source detection (source_health.py)."""

from datetime import datetime, timedelta

from ma_signal_monitor.source_health import flag_silent_sources


def _entry(
    *,
    first_attempt_at,
    last_persisted_at=None,
    last_status="ok",
    last_error="",
    attempts=1,
):
    return {
        "first_attempt_at": first_attempt_at,
        "last_attempt_at": first_attempt_at,
        "last_persisted_at": last_persisted_at,
        "last_status": last_status,
        "last_error": last_error,
        "attempts": attempts,
    }


def test_never_persisted_and_old_enough_is_flagged(sample_config):
    now = datetime(2026, 8, 8)
    old = (now - timedelta(days=30)).isoformat()
    health = {
        "Test Feed": _entry(
            first_attempt_at=old, last_status="error", last_error="403 Forbidden"
        )
    }
    flagged = flag_silent_sources(health, sample_config, now=now)
    assert len(flagged) == 1
    assert flagged[0]["source_name"] == "Test Feed"
    assert "never persisted" in flagged[0]["reason"]
    assert "403 Forbidden" in flagged[0]["reason"]


def test_never_persisted_but_too_recent_is_not_flagged(sample_config):
    now = datetime(2026, 8, 8)
    recent = (now - timedelta(days=1)).isoformat()
    health = {"Test Feed": _entry(first_attempt_at=recent, last_status="empty")}
    assert flag_silent_sources(health, sample_config, now=now) == []


def test_previously_healthy_source_gone_quiet_is_flagged(sample_config):
    """A source that used to persist but hasn't in a while must be caught,
    not just a source that's never worked at all."""
    now = datetime(2026, 8, 8)
    long_ago = (now - timedelta(days=60)).isoformat()
    stale_success = (now - timedelta(days=20)).isoformat()
    health = {
        "Test Feed": _entry(
            first_attempt_at=long_ago,
            last_persisted_at=stale_success,
            last_status="empty",
        )
    }
    flagged = flag_silent_sources(health, sample_config, now=now)
    assert len(flagged) == 1
    assert "last persisted an item 20d ago" in flagged[0]["reason"]


def test_currently_healthy_source_not_flagged(sample_config):
    now = datetime(2026, 8, 8)
    long_ago = (now - timedelta(days=60)).isoformat()
    recent_success = (now - timedelta(days=1)).isoformat()
    health = {
        "Test Feed": _entry(first_attempt_at=long_ago, last_persisted_at=recent_success)
    }
    assert flag_silent_sources(health, sample_config, now=now) == []


def test_never_attempted_source_not_flagged(sample_config):
    """No entry in health at all (outside the lookback window, or just
    enabled) must not be treated as silent — it hasn't had a chance yet."""
    assert flag_silent_sources({}, sample_config, now=datetime(2026, 8, 8)) == []


def test_disabled_source_never_flagged(sample_config):
    sample_config.sources[0].enabled = False
    now = datetime(2026, 8, 8)
    old = (now - timedelta(days=30)).isoformat()
    health = {"Test Feed": _entry(first_attempt_at=old, last_status="error")}
    assert flag_silent_sources(health, sample_config, now=now) == []


def test_source_silent_days_is_configurable(sample_config):
    sample_config.source_silent_days = 90
    now = datetime(2026, 8, 8)
    old = (now - timedelta(days=30)).isoformat()
    health = {"Test Feed": _entry(first_attempt_at=old, last_status="error")}
    # 30d silent < the raised 90d threshold -> not flagged.
    assert flag_silent_sources(health, sample_config, now=now) == []
