from evals.relevance.watch_selection import run_replay


def test_watch_selection_current_vs_candidate_outcomes():
    report = run_replay()
    assert report["passed"], report["results"]
    assert len(report["results"]) == 13


def test_kff_and_scan_regressions_are_explicit():
    rows = {row["id"]: row for row in run_replay()["results"]}
    assert rows["kff-medigap-underwriting"]["current"]["score"] == 0.21
    assert rows["kff-medigap-underwriting"]["candidate_gate_off"]["score"] == 0.31
    assert rows["ordinary-scan-verb"]["current"]["matched_entities"] == ["SCAN"]
    assert rows["ordinary-scan-verb"]["candidate_gate_off"]["matched_entities"] == []
    assert rows["healthfirst-stars-filing"]["candidate_gate_off"]["drafted"] is True
    assert (
        rows["healthfirst-stars-filing"]["candidate_gate_off"]["matched_entities"] == []
    )
    assert rows["5ff220ff735bbf59"]["candidate_gate_off"]["matched_entities"] == [
        "SCAN"
    ]
    assert rows["942eef5fa2f856e6"]["candidate_gate_off"]["matched_entities"] == [
        "SCAN"
    ]
    assert rows["8d11fb0f6f8e63bc"]["candidate_gate_off"]["drafted"] is False


def test_replay_uses_saved_product_surfaces():
    report = run_replay()
    assert report["production_paths"] == {
        "persistence_classification_and_duplicates": "main._persist_stories",
        "saved_story_readback": "web.routes._story_view",
        "decoded_angles_facets": "web.routes._facet_view",
    }
    rows = {row["id"]: row for row in report["results"]}
    assert rows["kff-medigap-underwriting"]["candidate_gate_off"]["drafted"]
    assert rows["kff-medigap-underwriting"]["candidate_gate_off"]["briefing"]
    assert rows["scan-costco-reprint"]["candidate_gate_off"]["duplicate_of"] == (
        "5ff220ff735bbf59"
    )
    assert rows["5ff220ff735bbf59"]["candidate_gate_off"]["facet_types"] == {
        "categories": "list",
        "entities": "list",
        "states": "list",
    }


def test_benchmark_discloses_unknowns_and_provenance():
    report = run_replay()
    assert report["config_identity"]["production_flags_changed"] is False
    assert all(row["current"]["fetched"] == "unknown" for row in report["results"])
    assert "hypothetical gate on" in report["config_identity"]["gate_experiment"]
    assert all(
        row["label_provenance"] == "assistant_recommendation_not_owner_confirmed"
        for row in report["results"]
    )
    assert any("per-claim support gate" in limit for limit in report["coverage_limits"])
    assert any(
        "not a byte-for-byte copy" in limit for limit in report["coverage_limits"]
    )
    assert any(
        "PR77 owns the broader eligibility-gate evaluation" in limit
        for limit in report["coverage_limits"]
    )


def test_actual_baseline_and_candidate_are_separate_and_repeatable():
    from ma_signal_monitor.scoring import score_items
    from evals.relevance import watch_selection

    assert watch_selection.score_items is score_items
    first = run_replay()
    assert first == run_replay()
    rows = {row["id"]: row for row in first["results"]}
    assert rows["kff-medigap-underwriting"]["current"]["saved_draft"] is None
    saved = rows["kff-medigap-underwriting"]["candidate_gate_off"]["saved_draft"]
    assert "DRAFT" in saved["draft_paragraph"]
    assert "KFF Medicare reports" in saved["draft_paragraph"]
    assert all(row["current"]["archived"] for row in rows.values())


def test_missing_persistence_is_not_counted_as_success(monkeypatch):
    import pytest
    from evals.relevance import watch_selection

    monkeypatch.setattr(watch_selection, "_persist_stories", lambda *args: None)
    with pytest.raises((TypeError, KeyError, AttributeError)):
        run_replay()
