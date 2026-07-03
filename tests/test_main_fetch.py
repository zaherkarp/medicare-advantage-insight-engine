"""Tests for concurrent source fetching in the pipeline orchestrator."""

import threading
import time

from ma_signal_monitor import main as main_mod
from ma_signal_monitor.config import SourceConfig
from ma_signal_monitor.main import _fetch_all_sources
from ma_signal_monitor.models import RawFeedItem


def _source(name, type_="rss", enabled=True):
    return SourceConfig(
        name=name,
        type=type_,
        url=f"https://example.com/{name}",
        priority=3,
        enabled=enabled,
        tags=[],
    )


def _item(source_name, title):
    return RawFeedItem(
        source_name=source_name,
        source_type="rss",
        source_url=f"https://example.com/{source_name}",
        source_priority=3,
        source_tags=[],
        title=title,
        link=f"https://example.com/{source_name}/{title}",
        published="Mon, 01 Jan 2024 12:00:00 +0000",
        summary="",
    )


def _patch_fetcher(monkeypatch, fake):
    monkeypatch.setitem(main_mod._FETCHERS, "rss", fake)


def test_fetch_collects_all_sources_in_config_order(monkeypatch, sample_config):
    sample_config.sources = [_source("a"), _source("b"), _source("c")]
    sample_config.fetch_workers = 4

    def fake(source, **kwargs):
        # Later sources finish first: order must still follow sources.yaml.
        time.sleep(0.05 if source.name == "a" else 0.0)
        return [_item(source.name, "t")]

    _patch_fetcher(monkeypatch, fake)
    items = _fetch_all_sources(sample_config)
    assert [i.source_name for i in items] == ["a", "b", "c"]


def test_fetch_isolates_per_source_errors(monkeypatch, sample_config):
    sample_config.sources = [_source("good"), _source("bad"), _source("also-good")]
    sample_config.fetch_workers = 4

    def fake(source, **kwargs):
        if source.name == "bad":
            raise ValueError("boom")
        return [_item(source.name, "t")]

    _patch_fetcher(monkeypatch, fake)
    items = _fetch_all_sources(sample_config)
    assert [i.source_name for i in items] == ["good", "also-good"]


def test_fetch_runs_concurrently(monkeypatch, sample_config):
    sample_config.sources = [_source(f"s{i}") for i in range(4)]
    sample_config.fetch_workers = 4
    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake(source, **kwargs):
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.05)
        with lock:
            active["now"] -= 1
        return []

    _patch_fetcher(monkeypatch, fake)
    _fetch_all_sources(sample_config)
    assert active["max"] > 1


def test_fetch_workers_one_is_sequential(monkeypatch, sample_config):
    sample_config.sources = [_source("a"), _source("b")]
    sample_config.fetch_workers = 1
    threads = set()

    def fake(source, **kwargs):
        threads.add(threading.current_thread().name)
        return [_item(source.name, "t")]

    _patch_fetcher(monkeypatch, fake)
    items = _fetch_all_sources(sample_config)
    assert len(items) == 2
    assert threads == {threading.main_thread().name}


def test_fetch_skips_disabled_and_unknown_types(monkeypatch, sample_config):
    sample_config.sources = [
        _source("on"),
        _source("off", enabled=False),
        _source("weird", type_="carrier-pigeon"),
    ]
    sample_config.fetch_workers = 4

    def fake(source, **kwargs):
        return [_item(source.name, "t")]

    _patch_fetcher(monkeypatch, fake)
    items = _fetch_all_sources(sample_config)
    assert [i.source_name for i in items] == ["on"]
