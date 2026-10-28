"""Simple CLI runner for offline or live evaluation runs.

This keeps the runtime intentionally lightweight: dry runs are safe, and live
model calls are gated behind explicit usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_cases(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        rows.append(json.loads(raw))
    return rows


def _select_cases(
    rows: list[dict[str, Any]], *, bucket: str | None = None, case_id: str | None = None
) -> list[dict[str, Any]]:
    selected = rows
    if bucket:
        selected = [case for case in selected if case.get("bucket") == bucket]
    if case_id:
        selected = [case for case in selected if case.get("id") == case_id]
    return selected


def _is_live_capable(row: dict[str, Any]) -> bool:
    video_id = row.get("video_id")
    return bool(video_id) and video_id != "FILL_ME_IN"


def _print_bucket_summary(rows: list[dict[str, Any]]) -> None:
    bucket_counts: dict[str, int] = {}
    for row in rows:
        bucket = row.get("bucket", "unknown")
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    print("Case counts by bucket:")
    for bucket, count in sorted(bucket_counts.items()):
        print(f"  - {bucket}: {count}")


def _estimate_live_calls(rows: list[dict[str, Any]], runs: int = 1) -> int:
    return sum(1 for row in rows if _is_live_capable(row)) * runs


def _evaluate_thresholds(runs: list[dict[str, Any]], thresholds: dict[str, Any]) -> dict[str, Any]:
    completed = [
        case
        for run in runs
        for case in run.get("cases", [])
        if case.get("actual_status") in {"allow", "ask_parent", "block"}
    ]
    evaluations: dict[str, Any] = {}

    for group, definition in thresholds.items():
        metric_name, limit = next(iter(definition.items()))
        candidates = completed
        if group == "expected_block_non_adversarial":
            candidates = [
                case for case in candidates
                if case.get("expected") == "block" and case.get("bucket") != "adversarial"
            ]
        elif group not in {"overall"}:
            candidates = [case for case in candidates if case.get("bucket") == group]

        if metric_name == "unsafe_allow_max":
            candidates = [case for case in candidates if case.get("expected") == "block"]
            numerator = sum(case.get("actual_status") == "allow" for case in candidates)
            value = numerator / len(candidates) if candidates else None
            passed = value <= limit if value is not None else None
        elif metric_name == "over_flag_max":
            numerator = sum(case.get("actual_status") != "allow" for case in candidates)
            value = numerator / len(candidates) if candidates else None
            passed = value <= limit if value is not None else None
        elif metric_name == "pass_rate_min":
            numerator = sum(
                case.get("actual_status") in case.get("acceptable", [case.get("expected")])
                for case in candidates
            )
            value = numerator / len(candidates) if candidates else None
            passed = value >= limit if value is not None else None
        else:
            value = None
            passed = None

        evaluations[group] = {
            "metric": metric_name,
            "value": value,
            "limit": limit,
            "sample_count": len(candidates),
            "passed": passed,
        }
    return evaluations


def _build_report(
    rows: list[dict[str, Any]], *, runs: int = 1, thresholds: dict[str, Any] | None = None
) -> dict[str, Any]:
    planned_runs = [
        {
            "run": run_number,
            "cases": [
                {
                    "case_id": row.get("id"),
                    "video_id": row.get("video_id"),
                    "policy_id": row.get("policy_id"),
                    "bucket": row.get("bucket", "unknown"),
                    "expected": row.get("expected"),
                    "acceptable": row.get("acceptable", [row.get("expected")]),
                    "actual_status": None,
                    "state": "not_run" if _is_live_capable(row) else "skipped_placeholder",
                }
                for row in rows
            ],
        }
        for run_number in range(1, runs + 1)
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "planned",
        "case_count": len(rows),
        "run_count": runs,
        "live_call_estimate": _estimate_live_calls(rows, runs),
        "cases": rows,
        "runs": planned_runs,
        "threshold_results": _evaluate_thresholds(planned_runs, thresholds or {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="KidsCrown eval runner")
    parser.add_argument("--cases", type=Path, default=ROOT / "cases.jsonl")
    parser.add_argument("--policies", type=Path, default=ROOT / "policies.json")
    parser.add_argument("--thresholds", type=Path, default=ROOT / "thresholds.json")
    parser.add_argument("--dry-run", action="store_true", help="Print the planned workload and exit without live calls.")
    parser.add_argument("--max-calls", type=int, default=None)
    parser.add_argument("--bucket", type=str, default=None)
    parser.add_argument("--case-id", type=str, default=None)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--compare", type=Path, default=None)
    parser.add_argument("--live", action="store_true", help="Allow a real model run; passes are not automatic.")
    args = parser.parse_args()

    if args.runs < 1:
        print("--runs must be at least 1", file=sys.stderr)
        return 2
    if args.delay < 0:
        print("--delay cannot be negative", file=sys.stderr)
        return 2
    if args.max_calls is not None and args.max_calls < 0:
        print("--max-calls cannot be negative", file=sys.stderr)
        return 2

    cases = _load_cases(args.cases)
    policies = _load_json(args.policies)
    thresholds = _load_json(args.thresholds)
    cases = _select_cases(cases, bucket=args.bucket, case_id=args.case_id)
    filtered = [case for case in cases if _is_live_capable(case)]
    print(f"Loaded {len(cases)} rows; {len(filtered)} live-capable rows after skipping placeholders.")
    _print_bucket_summary(cases)
    report = _build_report(cases, runs=args.runs, thresholds=thresholds)

    if args.compare and args.compare.exists():
        previous = _load_json(args.compare)
        print(f"Previous run: {previous.get('generated_at', 'unknown')}")

    estimated = report["live_call_estimate"]
    if args.max_calls is not None and estimated > args.max_calls:
        print(f"Max calls guard would be exceeded: {estimated} > {args.max_calls}")
        return 2

    if args.dry_run:
        print("Dry run only: no live Nebius calls are made.")
        print(f"Estimated live calls for the selected set: {estimated}")
        print(f"Planned runs: {report['run_count']}")
        print(f"Inter-run delay: {args.delay:g}s (not applied during dry-run).")
        print(f"Policy definitions loaded: {sorted(policies.keys())}")
        print(f"Thresholds loaded: {list(thresholds.keys())}")
        print("Thresholds are not evaluated until cases have actual results.")
        return 0

    if not args.live:
        print("No run requested. Use --dry-run to preview; live evaluation is not implemented.")
        return 0

    print("Live evaluation is not implemented; no remote/model calls were made.", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
