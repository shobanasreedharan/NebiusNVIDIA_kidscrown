import builtins
import json
import sys
import time

import pytest

from evals import run_evals


def _case(case_id, video_id, bucket="clean", expected="allow"):
    return {
        "id": case_id,
        "video_id": video_id,
        "policy_id": "sample",
        "child_age": 8,
        "expected": expected,
        "acceptable": [expected],
        "bucket": bucket,
        "notes": "runner test case",
    }


def _inputs(tmp_path, cases=None, thresholds=None):
    cases = cases or [
        _case("clean-one", "video-one"),
        _case("clean-placeholder", "FILL_ME_IN"),
        _case("gaming-two", "video-two", bucket="gaming", expected="block"),
        _case("missing-id", ""),
    ]
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text("\n".join(json.dumps(case) for case in cases), encoding="utf-8")
    policies_path = tmp_path / "policies.json"
    policies_path.write_text(json.dumps({"sample": {"family": "test policy", "child": None}}), encoding="utf-8")
    thresholds_path = tmp_path / "thresholds.json"
    thresholds_path.write_text(json.dumps(thresholds or {}), encoding="utf-8")
    return cases_path, policies_path, thresholds_path


def _run_main(monkeypatch, tmp_path, extra_args=(), cases=None, thresholds=None):
    cases_path, policies_path, thresholds_path = _inputs(tmp_path, cases, thresholds)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_evals.py",
            "--cases", str(cases_path),
            "--policies", str(policies_path),
            "--thresholds", str(thresholds_path),
            *extra_args,
        ],
    )
    return run_evals.main()


def test_case_loading_and_placeholder_filtering(monkeypatch, tmp_path, capsys):
    result = _run_main(monkeypatch, tmp_path, ("--dry-run",))

    output = capsys.readouterr().out
    assert result == 0
    assert "Loaded 4 rows; 2 live-capable rows" in output
    assert "Estimated live calls for the selected set: 2" in output


def test_bucket_filtering(monkeypatch, tmp_path, capsys):
    result = _run_main(monkeypatch, tmp_path, ("--dry-run", "--bucket", "gaming"))

    output = capsys.readouterr().out
    assert result == 0
    assert "Loaded 1 rows; 1 live-capable rows" in output
    assert "gaming: 1" in output
    assert "clean:" not in output


def test_case_id_filtering(monkeypatch, tmp_path, capsys):
    result = _run_main(monkeypatch, tmp_path, ("--dry-run", "--case-id", "gaming-two"))

    output = capsys.readouterr().out
    assert result == 0
    assert "Loaded 1 rows; 1 live-capable rows" in output
    assert "gaming: 1" in output


def test_max_calls_guard_counts_runs_and_skips_placeholders(monkeypatch, tmp_path, capsys):
    result = _run_main(monkeypatch, tmp_path, ("--dry-run", "--runs", "2", "--max-calls", "3"))

    output = capsys.readouterr().out
    assert result == 2
    assert "Max calls guard would be exceeded: 4 > 3" in output


def test_runs_are_represented_in_report_and_zero_is_rejected(monkeypatch, tmp_path, capsys):
    cases = [_case("case-one", "video-one"), _case("case-placeholder", "FILL_ME_IN")]
    report = run_evals._build_report(cases, runs=3)

    assert report["run_count"] == 3
    assert report["live_call_estimate"] == 3
    assert [run["run"] for run in report["runs"]] == [1, 2, 3]

    result = _run_main(monkeypatch, tmp_path, ("--dry-run", "--runs", "0"))
    assert result == 2
    assert "--runs must be at least 1" in capsys.readouterr().err


def test_delay_is_only_planned_and_dry_run_does_not_sleep(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(time, "sleep", lambda _seconds: pytest.fail("dry-run must not sleep"))

    result = _run_main(monkeypatch, tmp_path, ("--dry-run", "--delay", "0.25"))

    assert result == 0
    assert "Inter-run delay: 0.25s (not applied during dry-run)." in capsys.readouterr().out


def test_report_contains_per_run_case_details_without_fake_results(tmp_path):
    cases = [_case("case-one", "video-one"), _case("case-placeholder", "FILL_ME_IN")]
    thresholds = run_evals._load_json(run_evals.ROOT / "thresholds.json")

    report = run_evals._build_report(cases, runs=2, thresholds=thresholds)

    assert report["case_count"] == 2
    assert report["live_call_estimate"] == 2
    assert len(report["runs"]) == 2
    assert report["runs"][0]["cases"][0]["case_id"] == "case-one"
    assert report["runs"][0]["cases"][0]["actual_status"] is None
    assert report["runs"][0]["cases"][1]["state"] == "skipped_placeholder"
    assert all(item["passed"] is None for item in report["threshold_results"].values())


def test_threshold_evaluation_uses_thresholds_json():
    thresholds = run_evals._load_json(run_evals.ROOT / "thresholds.json")
    run = {
        "run": 1,
        "cases": [
            {**_case("unsafe", "v1", bucket="gaming", expected="block"), "actual_status": "allow"},
            {**_case("clean-flag", "v2"), "actual_status": "ask_parent"},
            {**_case("family-pass", "v3", bucket="family_rules"), "actual_status": "allow"},
            {**_case("injection-pass", "v4", bucket="injection", expected="block"), "actual_status": "block"},
        ],
    }

    result = run_evals._evaluate_thresholds([run], thresholds)

    assert result["overall"]["value"] == 0.5 and result["overall"]["passed"] is False
    assert result["expected_block_non_adversarial"]["passed"] is False
    assert result["clean"]["passed"] is False
    assert result["family_rules"]["passed"] is True
    assert result["injection"]["passed"] is True


def test_compare_reports_previous_run_when_report_exists(monkeypatch, tmp_path, capsys):
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps({"generated_at": "2026-09-30T12:00:00+00:00"}), encoding="utf-8")

    result = _run_main(monkeypatch, tmp_path, ("--dry-run", "--compare", str(previous_path)))

    assert result == 0
    assert "Previous run: 2026-09-30T12:00:00+00:00" in capsys.readouterr().out


def test_dry_run_never_imports_remote_model_dependencies(monkeypatch, tmp_path):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "openai" or name.startswith("app.nebius"):
            raise AssertionError(f"unexpected model import during dry-run: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    assert _run_main(monkeypatch, tmp_path, ("--dry-run",)) == 0


def test_live_mode_fails_clearly_without_making_remote_calls(monkeypatch, tmp_path, capsys):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "openai" or name.startswith("app.nebius"):
            raise AssertionError(f"unexpected model import in unimplemented live mode: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = _run_main(monkeypatch, tmp_path, ("--live",))

    assert result == 3
    assert "Live evaluation is not implemented; no remote/model calls were made." in capsys.readouterr().err