"""Tests for the source-yield review policy."""

from ma_signal_monitor.source_review import flag_low_yield_sources


def _stat(source, total, relevant, max_score):
    return {
        "source": source,
        "total": total,
        "relevant": relevant,
        "yield": (relevant / total) if total else 0.0,
        "mean_score": 0.0,
        "max_score": max_score,
        "last_fetched": "2024-01-01",
    }


def test_flags_low_yield_after_sample(sample_config):
    stats = [_stat("Junk Feed", total=40, relevant=0, max_score=0.04)]
    flagged = flag_low_yield_sources(stats, sample_config)
    assert len(flagged) == 1
    assert flagged[0]["source"] == "Junk Feed"
    assert "0/40" in flagged[0]["reason"]


def test_small_sample_not_flagged(sample_config):
    # Below min_sample (25) even with zero relevant.
    stats = [_stat("New Feed", total=5, relevant=0, max_score=0.01)]
    assert flag_low_yield_sources(stats, sample_config) == []


def test_occasional_strong_hit_spared(sample_config):
    # Low yield but one strong story → not flagged.
    stats = [_stat("Spiky Feed", total=50, relevant=1, max_score=0.8)]
    assert flag_low_yield_sources(stats, sample_config) == []


def test_healthy_source_not_flagged(sample_config):
    stats = [_stat("Good Feed", total=50, relevant=30, max_score=0.9)]
    assert flag_low_yield_sources(stats, sample_config) == []
