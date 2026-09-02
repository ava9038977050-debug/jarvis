#!/usr/bin/env python3
"""End-to-end tests for the installed SubagentStop command hook."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HOOK = ROOT / ".codex" / "hooks" / "validate_epistemic_contract.py"


def valid_message() -> str:
    contract = {
        "schema": "jarvis.epistemic.v1",
        "status": "VERIFIED",
        "evidence": [
            {
                "claim": "Корневая инструкция существует",
                "source_type": "local_file",
                "source": "<repo-root>/AGENTS.md",
                "locator": "раздел Протокол достоверности",
                "checked_at": None,
            }
        ],
        "assumptions": [],
        "critical_gaps": [],
        "conflicts": [],
        "boundaries": ["Проверяется наличие файла, но не вся семантика утверждений"],
        "minimal_question": None,
    }
    return (
        "<EPISTEMIC_CONTRACT>\n"
        + json.dumps(contract, ensure_ascii=False)
        + "\n</EPISTEMIC_CONTRACT>\nРезультат."
    )


def invoke(message: str, stop_hook_active: bool = False) -> subprocess.CompletedProcess[str]:
    event = {
        "hook_event_name": "SubagentStop",
        "agent_type": "executive_lawyer",
        "last_assistant_message": message,
        "stop_hook_active": stop_hook_active,
    }
    return subprocess.run(
        ["python", str(HOOK)],
        cwd=ROOT,
        input=json.dumps(event, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )


def invoke_configured_windows_command(message: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    config = json.loads((ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    command = config["hooks"]["SubagentStop"][0]["hooks"][0]["commandWindows"]
    event = {
        "hook_event_name": "SubagentStop",
        "agent_type": "executive_lawyer",
        "last_assistant_message": message,
        "stop_hook_active": False,
    }
    return subprocess.run(
        command,
        cwd=cwd,
        input=json.dumps(event, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        shell=True,
        check=False,
    )


class HookIntegrationTests(unittest.TestCase):
    def test_valid_result_passes_silently(self) -> None:
        completed = invoke(valid_message())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_invalid_result_is_blocked_for_retry(self) -> None:
        completed = invoke("Уверенный ответ без контракта.")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("rejected", payload["reason"])

    def test_second_invalid_result_stops_fail_closed(self) -> None:
        completed = invoke("Снова без контракта.", stop_hook_active=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertIs(payload["continue"], False)
        self.assertIn("stopped", payload["systemMessage"])

    def test_validator_failure_also_blocks(self) -> None:
        completed = subprocess.run(
            ["python", str(HOOK)],
            cwd=ROOT,
            input="not-json",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("failed closed", payload["reason"])

    def test_configured_command_resolves_from_nested_cwd(self) -> None:
        nested = ROOT / "agents" / "executive-director"
        completed = invoke_configured_windows_command(valid_message(), nested)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
