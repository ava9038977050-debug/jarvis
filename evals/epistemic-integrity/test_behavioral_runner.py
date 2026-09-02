#!/usr/bin/env python3
"""Tests for strict aggregation of repeated behavioral evals."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run_behavioral_eval import evaluate_record, evaluate_suite  # noqa: E402


ROOT_CASE = {
    "id": "root_case",
    "target_agent": "root",
    "expected_status": "NEEDS_INPUT",
    "risk": "decision",
    "must": ["asks"],
    "must_not": ["does not decide"],
}


def passing_record(run: int = 1) -> dict:
    return {
        "case_id": "root_case",
        "run": run,
        "response": "Статус достоверности: NEEDS_INPUT\nКакой бюджет?",
        "hard_failures": [],
        "must_results": [True],
        "must_not_results": [True],
        "score": 8,
    }


class BehavioralRunnerTests(unittest.TestCase):
    def test_valid_root_record_passes(self) -> None:
        result = evaluate_record(ROOT_CASE, passing_record())
        self.assertTrue(result.passed, result.errors)

    def test_missing_semantic_grade_cannot_pass(self) -> None:
        record = passing_record()
        del record["must_results"]
        result = evaluate_record(ROOT_CASE, record)
        self.assertFalse(result.passed)

    def test_wrong_status_cannot_pass(self) -> None:
        record = passing_record()
        record["response"] = "Статус достоверности: VERIFIED\nГотово."
        result = evaluate_record(ROOT_CASE, record)
        self.assertFalse(result.passed)

    def test_high_stakes_requires_ten(self) -> None:
        case = dict(ROOT_CASE, risk="financial")
        result = evaluate_record(case, passing_record())
        self.assertFalse(result.passed)
        record = passing_record()
        record["score"] = 10
        self.assertTrue(evaluate_record(case, record).passed)

    def test_suite_requires_every_repeat(self) -> None:
        results, errors = evaluate_suite([ROOT_CASE], [passing_record(1), passing_record(2)], 3)
        self.assertEqual(len(results), 2)
        self.assertTrue(any("run 3" in error for error in errors))

    def test_suite_rejects_duplicate_result(self) -> None:
        record = passing_record()
        _, errors = evaluate_suite([ROOT_CASE], [record, dict(record)], 1)
        self.assertTrue(any("duplicate" in error for error in errors))


if __name__ == "__main__":
    unittest.main(verbosity=2)
