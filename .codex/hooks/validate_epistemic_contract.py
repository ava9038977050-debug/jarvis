#!/usr/bin/env python3
"""SubagentStop hook enforcing the Jarvis epistemic-result contract."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path


VALIDATOR_SHA256 = "844edc891ba9039f8d4a4e09a23d6370c302048fd4cadd57d5e6fa678401b5e4"


def repo_root() -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return Path(completed.stdout.strip()).resolve()


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    event: dict = {}
    try:
        root = repo_root()
        validator_dir = root / "evals" / "epistemic-integrity"
        validator_path = validator_dir / "contract_validation.py"
        actual_hash = hashlib.sha256(validator_path.read_bytes()).hexdigest()
        if actual_hash != VALIDATOR_SHA256:
            raise RuntimeError(
                "contract validator hash mismatch; review it and update the pinned hook hash"
            )
        sys.path.insert(0, str(validator_dir))
        from contract_validation import rejection_payload, validate_message

        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise ValueError("hook input must be a JSON object")
        message = event.get("last_assistant_message")
        if not isinstance(message, str):
            errors = ["last_assistant_message must be a string"]
        else:
            _, errors = validate_message(message, root)
        if errors:
            emit(rejection_payload(errors, bool(event.get("stop_hook_active"))))
        return 0
    except Exception as exc:  # fail closed while preserving the hook output protocol
        if bool(event.get("stop_hook_active")):
            emit(
                {
                    "continue": False,
                    "stopReason": f"Epistemic validator failed closed: {exc}",
                    "systemMessage": "Specialist result was stopped because validation could not complete.",
                }
            )
        else:
            emit({"decision": "block", "reason": f"Epistemic validator failed closed: {exc}"})
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
