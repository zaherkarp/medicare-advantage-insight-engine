from evals.relevance.product_replay import run_replay


def test_product_replay_matches_declared_surface_outcomes():
    report = run_replay()
    assert report["passed"], report["results"]
    assert len(report["results"]) == 6
    assert report["alert_duplicates_suppressed"] == 1


def test_conflicting_figures_never_become_draft_candidate():
    report = run_replay()
    conflict = next(r for r in report["results"] if r["id"] == "conflicting-figures")
    assert conflict["observed"]["alert"] is True
    assert conflict["observed"]["draft_candidate"] is False
