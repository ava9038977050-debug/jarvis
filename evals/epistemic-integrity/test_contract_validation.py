#!/usr/bin/env python3
"""Unit and negative tests for the epistemic contract validator."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from contract_validation import rejection_payload, validate_message  # noqa: E402


def contract_message(contract: dict, body: str = "Профессиональный результат.") -> str:
    return (
        "<EPISTEMIC_CONTRACT>\n"
        + json.dumps(contract, ensure_ascii=False, indent=2)
        + "\n</EPISTEMIC_CONTRACT>\n"
        + body
    )


def verified_contract() -> dict:
    return {
        "schema": "jarvis.epistemic.v1",
        "status": "VERIFIED",
        "evidence": [
            {
                "claim": "Стандарт существует",
                "source_type": "local_file",
                "source": "<repo-root>/knowledge/epistemic-integrity-standard.md",
                "locator": "раздел 5",
                "checked_at": None,
            }
        ],
        "assumptions": [],
        "critical_gaps": [],
        "conflicts": [],
        "boundaries": ["Не доказывает истинность внешних фактов"],
        "minimal_question": None,
    }


class ContractValidationTests(unittest.TestCase):
    def assert_valid(self, contract: dict, body: str = "Результат.") -> None:
        _, errors = validate_message(contract_message(contract, body), ROOT)
        self.assertEqual(errors, [])

    def assert_invalid(self, contract: dict, fragment: str, body: str = "Результат.") -> None:
        _, errors = validate_message(contract_message(contract, body), ROOT)
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_verified_valid(self) -> None:
        self.assert_valid(verified_contract())

    def test_conditional_valid(self) -> None:
        contract = verified_contract()
        contract["status"] = "CONDITIONAL"
        contract["assumptions"] = [
            {"statement": "Черновик", "impact": "Не утверждён", "verification": "Подтвердить у Юрия"}
        ]
        self.assert_valid(contract)

    def test_needs_input_valid(self) -> None:
        contract = verified_contract()
        contract["status"] = "NEEDS_INPUT"
        contract["evidence"] = []
        contract["critical_gaps"] = [
            {"missing": "Юрисдикция", "impact": "Меняет норму", "request": "Назвать страну"}
        ]
        contract["minimal_question"] = "Какая юрисдикция применима?"
        self.assert_valid(contract, "Можно установить только перечень нужных данных.")

    def test_contract_must_be_first_and_unique(self) -> None:
        message = "Вступление\n" + contract_message(verified_contract())
        _, errors = validate_message(message, ROOT)
        self.assertIn("epistemic contract must be the first result block", errors)
        duplicate = contract_message(verified_contract()) + contract_message(verified_contract())
        _, errors = validate_message(duplicate, ROOT)
        self.assertIn("result must contain exactly one epistemic contract", errors)

    def test_rejects_status_echo_and_malformed_json(self) -> None:
        _, errors = validate_message("Статус: VERIFIED | CONDITIONAL | NEEDS_INPUT", ROOT)
        self.assertTrue(errors)
        _, errors = validate_message("<EPISTEMIC_CONTRACT>{bad}</EPISTEMIC_CONTRACT>", ROOT)
        self.assertTrue(any("valid JSON" in error for error in errors))

    def test_verified_rejects_assumption(self) -> None:
        contract = verified_contract()
        contract["assumptions"] = [
            {"statement": "Неизвестно", "impact": "Меняет ответ", "verification": "Уточнить"}
        ]
        self.assert_invalid(contract, "VERIFIED forbids")

    def test_conditional_requires_evidence_and_assumption(self) -> None:
        contract = verified_contract()
        contract["status"] = "CONDITIONAL"
        contract["evidence"] = []
        self.assert_invalid(contract, "CONDITIONAL requires")

    def test_needs_input_rejects_final_decision(self) -> None:
        contract = verified_contract()
        contract["status"] = "NEEDS_INPUT"
        contract["critical_gaps"] = [
            {"missing": "Бюджет", "impact": "Меняет выбор", "request": "Указать лимит"}
        ]
        contract["minimal_question"] = "Какой бюджет?"
        self.assert_invalid(contract, "must not contain a final", "Рекомендация: купить вариант А.")
        self.assert_invalid(contract, "must not contain a final", "Несмотря на пробел, рекомендую вариант А.")

    def test_needs_input_requires_one_question(self) -> None:
        contract = verified_contract()
        contract["status"] = "NEEDS_INPUT"
        contract["critical_gaps"] = [
            {"missing": "Данные", "impact": "Меняют вывод", "request": "Уточнить"}
        ]
        contract["minimal_question"] = "Какой бюджет? Какой срок?"
        self.assert_invalid(contract, "exactly one question")

    def test_rejects_missing_and_relative_local_files(self) -> None:
        contract = verified_contract()
        contract["evidence"][0]["source"] = "knowledge/epistemic-integrity-standard.md"
        self.assert_invalid(contract, "must be absolute")
        contract = verified_contract()
        contract["evidence"][0]["source"] = "<repo-root>/does-not-exist.txt"
        self.assert_invalid(contract, "does not identify an existing file")

    def test_rejects_repo_traversal(self) -> None:
        contract = verified_contract()
        contract["evidence"][0]["source"] = "<repo-root>/../outside.txt"
        self.assert_invalid(contract, "escapes <repo-root>")

    def test_official_web_requires_https_and_date(self) -> None:
        contract = verified_contract()
        item = contract["evidence"][0]
        item.update({"source_type": "official_web", "source": "http://example.test/rule", "checked_at": None})
        self.assert_invalid(contract, "HTTPS URL")
        self.assert_invalid(contract, "required for official_web")

    def test_rejects_unknown_fields(self) -> None:
        contract = verified_contract()
        contract["confidence"] = 0.9
        self.assert_invalid(contract, "top-level fields mismatch")

    def test_retry_payload_blocks_once_then_stops(self) -> None:
        first = rejection_payload(["bad contract"], False)
        self.assertEqual(first["decision"], "block")
        second = rejection_payload(["bad contract"], True)
        self.assertIs(second["continue"], False)
        self.assertIn("stopped", second["systemMessage"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
