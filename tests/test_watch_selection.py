from evals.relevance.watch_selection import run_replay


def test_watch_selection_current_vs_candidate_outcomes():
    report = run_replay()
    assert report["passed"], report["results"]
    assert len(report["results"]) == 8


def test_kff_and_scan_regressions_are_explicit():
    rows = {row["id"]: row for row in run_replay()["results"]}
    assert rows["kff-medigap-underwriting"]["current"]["score"] == 0.21
    assert rows["kff-medigap-underwriting"]["candidate"]["score"] == 0.31
    assert rows["ordinary-scan-verb"]["current"]["matched_entities"] == ["SCAN"]
    assert rows["ordinary-scan-verb"]["candidate"]["matched_entities"] == []
    assert rows["healthfirst-stars-filing"]["candidate"]["drafted"] is True


def test_benchmark_discloses_unknowns_and_provenance():
    report = run_replay()
    assert report["config_identity"]["production_flags_changed"] is False
    assert all(row["current"]["fetched"] == "unknown" for row in report["results"])
    assert all(
        row["label_provenance"] == "assistant_recommendation_not_owner_confirmed"
        for row in report["results"]
    )
    assert any("claim support gate" in limit for limit in report["coverage_limits"])
