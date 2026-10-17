from evals.relevance.product_replay import run_replay


def test_product_replay_matches_declared_surface_outcomes():
    report = run_replay()
    assert report["passed"], report["results"]
    assert len(report["results"]) == 6
    assert report["archive_duplicates_assigned"] == 1
    assert report["alert_duplicates_suppressed"] == 1


def test_replay_exercises_production_paths_and_dates():
    report = run_replay()
    assert report["production_paths"]["scoring_and_eligibility"] == "score_items"
    assert report["production_paths"]["archive_duplicate_assignment"] == (
        "assign_story_duplicates"
    )
    assert report["production_paths"]["angles_cards"] == "build_angles"
    assert report["angles_card_count"] > 0
    assert all(row["event_date"] != row["first_seen"] for row in report["results"])
    assert {row["rating_year"] for row in report["results"]} == {2027}


def test_missing_claim_support_guard_is_reported_as_product_gap():
    report = run_replay()
    assert report["drafting_guard"]["available"] is False
    assert report["drafting_guard"]["status"] == "product_gap"
    conflict = next(r for r in report["results"] if r["id"] == "conflicting-figures")
    assert conflict["observed"]["alert"] is True
    assert "draft_candidate" not in conflict["observed"]
