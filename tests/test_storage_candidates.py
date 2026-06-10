"""Tests for candidate domain/source persistence and discovery queries."""

from ma_signal_monitor.discovery.harvest import DomainStat


def _stat(domain, seen=1, score=0.5):
    return DomainStat(
        domain=domain,
        times_seen=seen,
        relevance_score=score,
        example_link=f"https://{domain}/x",
        example_story_id="story-1",
    )


def test_candidate_domain_accumulates(temp_db):
    temp_db.upsert_candidate_domain(_stat("a.test", seen=1, score=0.4))
    temp_db.upsert_candidate_domain(_stat("a.test", seen=2, score=0.6))
    rows = temp_db.domains_due_for_discovery(min_times_seen=1)
    assert len(rows) == 1
    assert rows[0]["domain"] == "a.test"
    assert rows[0]["times_seen"] == 3
    assert abs(rows[0]["relevance_score"] - 1.0) < 1e-9


def test_domains_due_filters_and_orders(temp_db):
    temp_db.upsert_candidate_domain(_stat("low.test", seen=1, score=0.2))
    temp_db.upsert_candidate_domain(_stat("hi.test", seen=5, score=4.0))
    temp_db.upsert_candidate_domain(_stat("mid.test", seen=3, score=1.5))
    # Drop one-offs below min_times_seen, order by score desc.
    rows = temp_db.domains_due_for_discovery(min_times_seen=2)
    domains = [r["domain"] for r in rows]
    assert domains == ["hi.test", "mid.test"]


def test_mark_domain_checked_excludes_from_due(temp_db):
    temp_db.upsert_candidate_domain(_stat("a.test", seen=3, score=1.0))
    temp_db.mark_domain_checked("a.test")
    # Just checked → not due again until recheck window elapses.
    assert temp_db.domains_due_for_discovery(min_times_seen=1, recheck_days=14) == []


def test_candidate_source_dedupe_and_status_rules(temp_db):
    temp_db.upsert_candidate_source(
        feed_url="https://x.test/feed", domain="x.test", feed_title="X", status="new"
    )
    # Re-discovering refreshes stats and may upgrade a 'new' row.
    temp_db.upsert_candidate_source(
        feed_url="https://x.test/feed",
        domain="x.test",
        feed_title="X Updated",
        times_seen=9,
        relevance_score=5.0,
        status="auto_promoted",
    )
    assert temp_db.count_candidate_sources() == 1
    row = temp_db.list_candidate_sources()[0]
    assert row["feed_title"] == "X Updated"
    assert row["status"] == "auto_promoted"

    # An operator decision is preserved against later re-discovery.
    cid = row["id"]
    temp_db.set_candidate_status(cid, "rejected")
    temp_db.upsert_candidate_source(
        feed_url="https://x.test/feed", domain="x.test", status="new"
    )
    assert temp_db.get_candidate_source(cid)["status"] == "rejected"


def test_get_promoted_sources(temp_db):
    temp_db.upsert_candidate_source(
        feed_url="https://a.test/f", domain="a.test", status="new"
    )
    temp_db.upsert_candidate_source(
        feed_url="https://b.test/f", domain="b.test", status="promoted"
    )
    temp_db.upsert_candidate_source(
        feed_url="https://c.test/f", domain="c.test", status="auto_promoted"
    )
    urls = {r["feed_url"] for r in temp_db.get_promoted_sources()}
    assert urls == {"https://b.test/f", "https://c.test/f"}


def test_cleanup_prunes_dormant_candidates(temp_db):
    temp_db.upsert_candidate_domain(_stat("old.test", seen=1, score=0.3))
    temp_db.upsert_candidate_source(feed_url="https://old.test/f", domain="old.test")
    # Force the rows to look old.
    conn = temp_db._get_conn()
    conn.execute("UPDATE candidate_domains SET last_seen_at = '2000-01-01T00:00:00'")
    conn.execute("UPDATE candidate_sources SET last_seen_at = '2000-01-01T00:00:00'")
    conn.commit()

    temp_db.cleanup_old_records(candidate_retention_days=180)
    assert temp_db.count_candidate_sources() == 0
    assert temp_db.domains_due_for_discovery(min_times_seen=1) == []
