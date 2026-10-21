"""Synthetic protocol checks; these are not actual model evaluation results."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("evaluation_cli", Path(__file__).parents[1] / "scripts/evaluate.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def setup_protocol(tmp_path, monkeypatch):
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals/dev-v1.json").write_bytes(b'{"synthetic":"development"}\r\n')
    (tmp_path / "evals/holdout-v1.json").write_bytes(b'{"synthetic":"old exposed holdout"}\r\n')
    (tmp_path / "evals/holdout-v2.json").write_bytes(b'{"synthetic":"fresh holdout"}\n')
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "source_hashes", lambda: {"product.py": "verified-test-source-hash"})
    report = {"split": "dev", "evaluation_revision": "v2", "stopped_early": False,
        "source_hashes": cli.source_hashes(), "model": "synthetic-test-model", "protocol": "test-only",
        "metrics": {"requested": 40, "executed": 40, "task_completion_rate": .95,
                    "forbidden_effect_count": 0, "policy_citation_automatic_rate": 1.0}}
    report_path = tmp_path / "synthetic-dev-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path, report


def test_new_freeze_preserves_exposed_record_and_checks_exact_fresh_dataset(tmp_path, monkeypatch):
    report_path, _report = setup_protocol(tmp_path, monkeypatch)
    original = b'{"historical":"must stay exact"}\r\n'
    cli.freeze_path("v1").write_bytes(original)
    cli.freeze(report_path, "v2")
    assert cli.freeze_path("v1").read_bytes() == original
    frozen = json.loads(cli.freeze_path("v2").read_text(encoding="utf-8"))
    assert frozen["dev_dataset"] == "evals/dev-v1.json"
    assert frozen["holdout_dataset"] == "evals/holdout-v2.json"
    settings = SimpleNamespace(model_name="synthetic-test-model", model_provider="test-only")
    cli.verify_freeze(settings, "v2")
    # Even a newline-only dataset change invalidates the frozen result lineage.
    cli.dataset_path("holdout", "v2").write_bytes(b'{"synthetic":"fresh holdout"}\r\n')
    with pytest.raises(SystemExit, match="changed"):
        cli.verify_freeze(settings, "v2")


def test_freeze_cannot_be_replaced_by_a_later_better_report(tmp_path, monkeypatch):
    report_path, _report = setup_protocol(tmp_path, monkeypatch)
    cli.freeze(report_path, "v2")
    original = cli.freeze_path("v2").read_bytes()
    with pytest.raises(SystemExit, match="immutable"):
        cli.freeze(report_path, "v2")
    assert cli.freeze_path("v2").read_bytes() == original


def test_changed_source_or_old_revision_dev_cannot_authorize_new_holdout(tmp_path, monkeypatch):
    report_path, report = setup_protocol(tmp_path, monkeypatch)
    report["source_hashes"] = {"product.py": "before-engineering-fix"}
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(SystemExit, match="changed after dev"):
        cli.freeze(report_path, "v2")
    report["source_hashes"] = cli.source_hashes()
    report["evaluation_revision"] = "v1"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(SystemExit, match="gates"):
        cli.freeze(report_path, "v2")
    assert not cli.freeze_path("v2").exists()
