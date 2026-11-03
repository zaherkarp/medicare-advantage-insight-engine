"""Maintenance safety and existing-output regression tests, with synthetic DBs."""

import copy
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ma_signal_monitor import archive_repair as repair
from ma_signal_monitor.config import load_config
from ma_signal_monitor.models import NormalizedItem, ScoredItem
from ma_signal_monitor.storage import StateStore
from ma_signal_monitor.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "replay@example.invalid")
    monkeypatch.setenv("MA_ELIGIBILITY_GATE", "false")
    return load_config(ROOT, include_discovered_sources=False)


@pytest.fixture
def archive(tmp_path, config):
    store = StateStore(tmp_path / "archive.db")
    now = datetime.utcnow() - timedelta(hours=1)
    for item_id, title, summary, category in [
        (
            "vendor",
            "Vera Health launches a clinical search platform",
            "The platform scans clinical publications.",
            "competitive_strategy",
        ),
        (
            "lawsuit",
            "SCAN Health Plan challenges CMS Star Ratings",
            "Medicare Advantage ratings litigation.",
            "policy_regulatory",
        ),
        ("other", "Unrelated archived story", "", "uncategorized"),
    ]:
        item = NormalizedItem(
            item_id=item_id,
            source_name="Synthetic fixture",
            source_type="fixture",
            source_priority=3,
            source_tags=[],
            title=title,
            summary=summary,
            link=f"https://example.invalid/{item_id}",
            published_date=now,
            fetched_at=now,
        )
        scored = ScoredItem(
            item=item,
            relevance_score=0.8,
            matched_entities=["SCAN"],
            matched_categories=[category],
        )
        store.upsert_story(
            scored,
            category,
            public_draft={"draft_paragraph": "STALE DRAFT"},
            states=["CA"],
            duplicate_of="vendor" if item_id == "other" else None,
        )
    store.add_feedback(
        "vendor", "irrelevant", channel="owner", source_ref="fixture-feedback"
    )
    store.save_digest(
        "2026-11-01", "2026-11-01T12:00:00", 3, "Historical digest", "Historical HTML"
    )
    yield store
    store.close()


def story(store, item_id="vendor"):
    return dict(store.get_story(item_id))


def test_read_only_plan_is_deterministic_and_never_initializes(archive, config):
    archive._get_conn().execute("DROP TABLE stories_fts")
    archive._get_conn().commit()
    archive.close()
    before = archive.db_path.read_bytes()
    plan = repair.plan_repair(archive.db_path, config, ["vendor"])
    assert plan == repair.plan_repair(archive.db_path, config, ["vendor"])
    assert archive.db_path.read_bytes() == before
    with sqlite3.connect(archive.db_path) as conn:
        assert not conn.execute(
            "SELECT name FROM sqlite_master WHERE name='stories_fts'"
        ).fetchall()
    assert plan["items"][0]["after"]["entities"] == "[]"
    assert plan["items"][0]["after"]["public_draft"] is None


@pytest.mark.parametrize("ids", [[], ["vendor", "missing"], ["vendor", "vendor"]])
def test_explicit_existing_unique_ids_required(archive, config, ids):
    before = story(archive)
    with pytest.raises(ValueError):
        repair.plan_repair(archive.db_path, config, ids)
    assert story(archive) == before


def test_typo_path_does_not_create_database(tmp_path, config):
    path = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        repair.plan_repair(path, config, ["vendor"])
    assert not path.exists()


def test_incomplete_schema_is_not_migrated(tmp_path, config):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE stories(item_id TEXT)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="schema"):
        repair.plan_repair(path, config, ["vendor"])
    assert path.read_bytes() == before


def test_rebuild_even_when_category_unchanged_preserves_other_records(archive, config):
    conn = archive._get_conn()
    conn.execute(
        "UPDATE stories SET primary_category='uncategorized', categories='[]' WHERE item_id='vendor'"
    )
    conn.commit()
    before, other = story(archive), story(archive, "other")
    feedback = [tuple(r) for r in conn.execute("SELECT * FROM feedback")]
    digests = [tuple(r) for r in conn.execute("SELECT * FROM digests")]
    plan = repair.plan_repair(archive.db_path, config, ["vendor"])
    assert (
        plan["items"][0]["before"]["categories"]
        == plan["items"][0]["after"]["categories"]
    )
    result = repair.apply_repair(archive.db_path, plan, plan["plan_sha256"], config)
    assert result["changed"] == 1
    after = story(archive)
    assert repair._derived(after) == plan["items"][0]["after"]
    assert repair._source_hash(before) == repair._source_hash(after)
    assert story(archive, "other") == other
    assert [tuple(r) for r in conn.execute("SELECT * FROM feedback")] == feedback
    assert [tuple(r) for r in conn.execute("SELECT * FROM digests")] == digests
    assert (
        repair.apply_repair(archive.db_path, plan, plan["plan_sha256"], config)[
            "changed"
        ]
        == 0
    )
    assert (
        repair.apply_repair(archive.db_path, plan, plan["plan_sha256"], rollback=True)[
            "changed"
        ]
        == 1
    )
    assert story(archive) == before
    assert (
        repair.apply_repair(archive.db_path, plan, plan["plan_sha256"], rollback=True)[
            "changed"
        ]
        == 0
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "Changed source"),
        ("duplicate_of", "lawsuit"),
        ("entities", '["Humana"]'),
    ],
)
def test_stale_source_or_derived_values_reject_entire_batch(
    archive, config, field, value
):
    plan = repair.plan_repair(archive.db_path, config, ["vendor", "lawsuit"])
    archive._get_conn().execute(
        f"UPDATE stories SET {field}=? WHERE item_id='vendor'", (value,)
    )
    archive._get_conn().commit()
    before = {i: story(archive, i) for i in ("vendor", "lawsuit")}
    with pytest.raises(ValueError, match="changed"):
        repair.apply_repair(archive.db_path, plan, plan["plan_sha256"], config)
    assert before == {i: story(archive, i) for i in before}


def test_code_config_and_plan_hash_guards(archive, config, monkeypatch):
    plan = repair.plan_repair(archive.db_path, config, ["vendor"])
    before = story(archive)
    with pytest.raises(ValueError, match="hash"):
        repair.apply_repair(archive.db_path, plan, "wrong", config)
    changed = copy.deepcopy(config)
    changed.min_relevance_score += 0.1
    with pytest.raises(ValueError, match="configuration"):
        repair.apply_repair(archive.db_path, plan, plan["plan_sha256"], changed)
    monkeypatch.setattr(repair, "_code_hash", lambda: "changed")
    with pytest.raises(ValueError, match="Code"):
        repair.apply_repair(archive.db_path, plan, plan["plan_sha256"], config)
    assert story(archive) == before


def test_recompute_rejects_altered_plan_even_with_its_hash(archive, config):
    plan = repair.plan_repair(archive.db_path, config, ["vendor"])
    plan["items"][0]["after"]["relevance_score"] = 1.0
    plan["plan_sha256"] = repair._hash(
        {k: v for k, v in plan.items() if k != "plan_sha256"}
    )
    with pytest.raises(ValueError, match="Recomputed"):
        repair.apply_repair(archive.db_path, plan, plan["plan_sha256"], config)


def test_second_row_failure_rolls_back_first(archive, config):
    plan = repair.plan_repair(archive.db_path, config, ["lawsuit", "vendor"])
    conn = archive._get_conn()
    conn.execute(
        "CREATE TRIGGER fail_vendor BEFORE UPDATE ON stories WHEN NEW.item_id='vendor' BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"
    )
    conn.commit()
    before = {i: story(archive, i) for i in ("lawsuit", "vendor")}
    with pytest.raises(sqlite3.IntegrityError):
        repair.apply_repair(archive.db_path, plan, plan["plan_sha256"], config)
    assert before == {i: story(archive, i) for i in before}


def test_rollback_refuses_later_changes(archive, config):
    plan = repair.plan_repair(archive.db_path, config, ["vendor"])
    repair.apply_repair(archive.db_path, plan, plan["plan_sha256"], config)
    archive._get_conn().execute(
        "UPDATE stories SET public_draft='later edit' WHERE item_id='vendor'"
    )
    archive._get_conn().commit()
    with pytest.raises(ValueError, match="Derived"):
        repair.apply_repair(archive.db_path, plan, plan["plan_sha256"], rollback=True)
    assert story(archive)["public_draft"] == "later edit"


def test_drafting_failure_aborts_instead_of_clearing(archive, config, monkeypatch):
    before = story(archive, "lawsuit")

    def fail(*args):
        raise RuntimeError("draft failure")

    monkeypatch.setattr(repair, "draft_alert", fail)
    with pytest.raises(RuntimeError, match="draft failure"):
        repair.plan_repair(archive.db_path, config, ["lawsuit"])
    assert story(archive, "lawsuit") == before


def test_null_date_and_priority_zero_preserved(archive, config):
    archive._get_conn().execute(
        "UPDATE stories SET published_date=NULL, source_priority=0 WHERE item_id='vendor'"
    )
    archive._get_conn().commit()
    plan = repair.plan_repair(archive.db_path, config, ["vendor"])
    repair.apply_repair(archive.db_path, plan, plan["plan_sha256"], config)
    assert story(archive)["source_priority"] == 0
    assert story(archive)["published_date"] is None
    assert story(archive)["relevance_score"] == 0


def test_invalid_fetch_date_fails_closed(archive, config):
    archive._get_conn().execute(
        "UPDATE stories SET fetched_at='' WHERE item_id='vendor'"
    )
    archive._get_conn().commit()
    with pytest.raises(ValueError, match="fetch date"):
        repair.plan_repair(archive.db_path, config, ["vendor"])


def test_rendered_angles_and_story_draft_change_only_after_apply(archive, config):
    client = TestClient(create_app(config, archive))
    before = client.get("/angles").text
    assert "Payer cascade" in before and "Vera Health" in before
    plan = repair.plan_repair(archive.db_path, config, ["vendor"])
    assert client.get("/angles").text == before
    repair.apply_repair(archive.db_path, plan, plan["plan_sha256"], config)
    after = client.get("/angles").text
    assert "Payer cascade" not in after and "Vera Health" not in after
    assert "STALE DRAFT" not in client.get("/story/vendor").text
    assert archive.get_story("vendor") is not None


def test_maintenance_config_does_not_open_discovery_archive(monkeypatch):
    from ma_signal_monitor import config as config_module

    monkeypatch.setenv("SEC_CONTACT_EMAIL", "replay@example.invalid")
    monkeypatch.setenv("DISCOVERY_ENABLED", "true")

    def forbidden(*args):
        raise AssertionError("must not initialize archive")

    monkeypatch.setattr(config_module, "_merge_promoted_sources", forbidden)
    assert load_config(ROOT, include_discovered_sources=False).discovery_enabled


def test_cli_plan_apply_rollback_and_no_overwrite(archive, config, tmp_path, capsys):
    path = tmp_path / "private-plan.json"
    base = ["--db", str(archive.db_path), "--root", str(ROOT), "--plan", str(path)]
    assert repair.main(["plan", *base, "--item-id", "vendor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert path.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        repair.main(["plan", *base, "--item-id", "vendor"])
    guard = ["--expected-sha", report["plan_sha256"]]
    assert repair.main(["apply", *base, *guard]) == 0
    assert repair.main(["rollback", *base, *guard]) == 0
