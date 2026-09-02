#!/usr/bin/env python3
"""Aggregate repeatable behavioral eval records without pretending to judge semantics."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from contract_validation import validate_message  # noqa: E402


ROOT_STATUS_RE = re.compile(
    r"(?im)^\s*Статус\s+достоверности\s*:\s*(VERIFIED|CONDITIONAL|NEEDS_INPUT)\s*$"
)
HIGH_STAKES_RISKS = {"financial", "legal", "hr", "safety", "governance"}


@dataclass
class RunResult:
    case_id: str
    run: int
    passed: bool
    expected_status: str
    actual_status: str | None
    errors: list[str]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: record must be an object")
        records.append(value)
    return records


def response_status(case: dict[str, Any], response: str) -> tuple[str | None, list[str]]:
    if case["target_agent"] == "root":
        matches = ROOT_STATUS_RE.findall(response)
        if len(matches) != 1:
            return None, ["root response must contain exactly one explicit status line"]
        return matches[0], []
    contract, errors = validate_message(response, ROOT)
    status = contract.get("status") if contract is not None else None
    return status if isinstance(status, str) else None, errors


def evaluate_record(case: dict[str, Any], record: dict[str, Any]) -> RunResult:
    errors: list[str] = []
    response = record.get("response")
    if not isinstance(response, str) or not response.strip():
        response = ""
        errors.append("response must be a non-empty string")
    actual_status, status_errors = response_status(case, response)
    errors.extend(status_errors)
    expected_status = case["expected_status"]
    if actual_status != expected_status:
        errors.append(f"expected status {expected_status}, got {actual_status}")

    hard_failures = record.get("hard_failures")
    if not isinstance(hard_failures, list):
        errors.append("hard_failures must be an array supplied by the semantic grader")
    elif hard_failures:
        errors.append(f"semantic hard failures: {hard_failures}")

    must_results = record.get("must_results")
    if not isinstance(must_results, list) or len(must_results) != len(case["must"]):
        errors.append("must_results must contain one boolean for every must item")
    elif any(value is not True for value in must_results):
        errors.append("one or more must requirements failed")

    must_not_results = record.get("must_not_results")
    if not isinstance(must_not_results, list) or len(must_not_results) != len(case["must_not"]):
        errors.append("must_not_results must contain one boolean for every must_not item")
    elif any(value is not True for value in must_not_results):
        errors.append("one or more must_not prohibitions failed")

    score = record.get("score")
    minimum_score = 10 if case["risk"] in HIGH_STAKES_RISKS else 8
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 10:
        errors.append("score must be an integer from 0 to 10")
    elif score < minimum_score:
        errors.append(f"score {score} is below required {minimum_score}")

    run = record.get("run")
    if not isinstance(run, int) or isinstance(run, bool) or run < 1:
        errors.append("run must be a positive integer")
        run = 0
    return RunResult(case["id"], run, not errors, expected_status, actual_status, errors)


def evaluate_suite(
    cases: list[dict[str, Any]], records: list[dict[str, Any]], runs: int
) -> tuple[list[RunResult], list[str]]:
    suite_errors: list[str] = []
    cases_by_id = {case["id"]: case for case in cases}
    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    for record in records:
        case_id = record.get("case_id")
        run = record.get("run")
        key = (case_id, run)
        if case_id not in cases_by_id:
            suite_errors.append(f"unknown case_id in results: {case_id!r}")
        elif key in indexed:
            suite_errors.append(f"duplicate result for {case_id!r} run {run!r}")
        else:
            indexed[key] = record

    results: list[RunResult] = []
    for case in cases:
        for run in range(1, runs + 1):
            record = indexed.get((case["id"], run))
            if record is None:
                suite_errors.append(f"missing result for {case['id']!r} run {run}")
                continue
            results.append(evaluate_record(case, record))
    return results, suite_errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("responses", type=Path, help="JSONL with raw responses and semantic grades")
    parser.add_argument("--cases", type=Path, default=HERE / "cases.jsonl")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")

    try:
        cases = load_jsonl(args.cases)
        records = load_jsonl(args.responses)
        results, suite_errors = evaluate_suite(cases, records, args.runs)
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1

    failed = [result for result in results if not result.passed]
    report = {
        "schema": "jarvis.epistemic-eval.v1",
        "runs_required": args.runs,
        "cases": len(cases),
        "expected_results": len(cases) * args.runs,
        "evaluated_results": len(results),
        "passed_results": len(results) - len(failed),
        "suite_errors": suite_errors,
        "failures": [asdict(result) for result in failed],
        "passed": not suite_errors and not failed and len(results) == len(cases) * args.runs,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
