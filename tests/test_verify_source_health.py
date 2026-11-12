"""Runtime receipt tests; never export archive data to Actions logs."""

from pathlib import Path
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify_source_health

from ma_signal_monitor.config import load_config
from ma_signal_monitor.models import SourceFetchOutcome
from ma_signal_monitor.storage import StateStore


def seed(root):
    path = root / load_config(root).db_path
    with_store = StateStore(path)
    run_id = with_store.start_run()
    with_store.log_source_fetches(
        run_id, [SourceFetchOutcome("Test Feed", "ok", n_items=2)], {"Test Feed": 2}
    )
    with_store.close()
    return path


def test_actual_diagnostic(project_root_with_config, capsys):
    path = seed(project_root_with_config)
    before = path.read_bytes()
    assert verify_source_health.main(["--root", str(project_root_with_config)]) == 0
    assert path.read_bytes() == before
    output = capsys.readouterr().out
    assert "diagnostic_exit=0; archive unchanged" in output
    assert "Test Feed" not in output


@pytest.mark.parametrize("code, expected", [(0, 0), (1, 0), (2, 2), (3, 2)])
def test_exit_policy(project_root_with_config, monkeypatch, capsys, code, expected):
    seed(project_root_with_config)

    def run(*args, **kwargs):
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 60
        return subprocess.CompletedProcess(args[0], code)

    monkeypatch.setattr(verify_source_health.subprocess, "run", run)
    assert (
        verify_source_health.main(["--root", str(project_root_with_config)]) == expected
    )
    assert ("::warning::" in capsys.readouterr().out) == (code == 1)


def test_missing_archive_not_created(project_root_with_config):
    path = project_root_with_config / load_config(project_root_with_config).db_path
    assert verify_source_health.main(["--root", str(project_root_with_config)]) == 2
    assert not path.exists()


def test_detects_mutation(project_root_with_config, monkeypatch, capsys):
    path = seed(project_root_with_config)

    def mutate(*args, **kwargs):
        path.write_bytes(path.read_bytes() + b"mutation")
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(verify_source_health.subprocess, "run", mutate)
    assert verify_source_health.main(["--root", str(project_root_with_config)]) == 2
    assert "bytes changed" in capsys.readouterr().out


def test_workflow_runs_between_writers():
    workflow = yaml.safe_load(
        (
            Path(__file__).resolve().parents[1] / ".github/workflows/deploy-pages.yml"
        ).read_text()
    )
    steps = workflow["jobs"]["build-deploy"]["steps"]
    names = [step.get("name") for step in steps]
    index = names.index("Verify read-only source-health diagnostic")
    assert names[index - 1] == "Ingest latest signals"
    assert (
        names[index + 1] == "Ingest giscus feedback (owner verdicts + crowd reactions)"
    )
    assert steps[index]["run"] == "python scripts/verify_source_health.py"
    assert "continue-on-error" not in steps[index]


def test_timeout_is_unavailable(project_root_with_config, monkeypatch):
    seed(project_root_with_config)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 60)

    monkeypatch.setattr(verify_source_health.subprocess, "run", timeout)
    assert verify_source_health.main(["--root", str(project_root_with_config)]) == 2


def test_wal_changes_are_detected(project_root_with_config, monkeypatch):
    path = seed(project_root_with_config)

    def mutate(*args, **kwargs):
        Path(f"{path}-wal").write_bytes(b"new data")
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(verify_source_health.subprocess, "run", mutate)
    assert verify_source_health.main(["--root", str(project_root_with_config)]) == 2
