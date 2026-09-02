#!/usr/bin/env python3
"""Canonical validator for Jarvis epistemic-result contracts."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SCHEMA = "jarvis.epistemic.v1"
START_TAG = "<EPISTEMIC_CONTRACT>"
END_TAG = "</EPISTEMIC_CONTRACT>"
STATUSES = {"VERIFIED", "CONDITIONAL", "NEEDS_INPUT"}
SOURCE_TYPES = {
    "user_input",
    "local_file",
    "tool_result",
    "official_web",
    "calculation",
    "proposal",
}
TOP_LEVEL_KEYS = {
    "schema",
    "status",
    "evidence",
    "assumptions",
    "critical_gaps",
    "conflicts",
    "boundaries",
    "minimal_question",
}
FINAL_DECISION_RE = re.compile(
    r"(?im)(?:^\s*(?:окончательн(?:ая|ый|ое)\s+)?(?:рекомендация|вывод|вердикт|решение)\s*:"
    r"|\b(?:рекомендую|утверждаю|выбирайте|покупайте|заключайте|подавайте)\b)"
)


class ContractError(ValueError):
    """Raised when a result contract is missing or invalid."""


def _nonempty_string(value: Any, field: str, errors: list[str]) -> bool:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field} must be a non-empty string")
        return False
    return True


def extract_contract(message: str) -> tuple[dict[str, Any], str]:
    """Extract exactly one leading contract and return it plus the role response."""
    if not isinstance(message, str) or not message.strip():
        raise ContractError("last_assistant_message is empty")
    if message.count(START_TAG) != 1 or message.count(END_TAG) != 1:
        raise ContractError("result must contain exactly one epistemic contract")

    start = message.index(START_TAG)
    end = message.index(END_TAG, start)
    if message[:start].strip():
        raise ContractError("epistemic contract must be the first result block")

    raw_json = message[start + len(START_TAG) : end].strip()
    try:
        contract = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ContractError(f"contract is not valid JSON: {exc.msg}") from exc
    if not isinstance(contract, dict):
        raise ContractError("contract JSON must be an object")
    remainder = message[end + len(END_TAG) :].strip()
    return contract, remainder


def _validate_local_file(value: str, repo_root: Path, field: str, errors: list[str]) -> None:
    normalized_root = repo_root.resolve()
    if value.startswith("<repo-root>/"):
        relative = value[len("<repo-root>/") :]
        candidate = (normalized_root / Path(relative)).resolve()
        try:
            candidate.relative_to(normalized_root)
        except ValueError:
            errors.append(f"{field} escapes <repo-root>")
            return
    else:
        candidate = Path(value)
        if not candidate.is_absolute():
            errors.append(f"{field} must be absolute or start with <repo-root>/")
            return
        candidate = candidate.resolve()
    if not candidate.is_file():
        errors.append(f"{field} does not identify an existing file: {value}")


def _validate_checked_at(value: Any, field: str, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        errors.append(f"{field} must be YYYY-MM-DD or null")
        return
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        errors.append(f"{field} must be a real ISO date YYYY-MM-DD or null")
        return
    if parsed.isoformat() != value:
        errors.append(f"{field} must use exact YYYY-MM-DD format")


def validate_contract(contract: dict[str, Any], repo_root: Path) -> list[str]:
    """Return all structural, status, and locally verifiable provenance errors."""
    errors: list[str] = []
    actual_keys = set(contract)
    if actual_keys != TOP_LEVEL_KEYS:
        missing = sorted(TOP_LEVEL_KEYS - actual_keys)
        extra = sorted(actual_keys - TOP_LEVEL_KEYS)
        errors.append(f"top-level fields mismatch; missing={missing}, extra={extra}")

    if contract.get("schema") != SCHEMA:
        errors.append(f"schema must equal {SCHEMA!r}")
    status = contract.get("status")
    if status not in STATUSES:
        errors.append(f"status must be one of {sorted(STATUSES)}")

    list_fields = ("evidence", "assumptions", "critical_gaps", "conflicts", "boundaries")
    for field in list_fields:
        if not isinstance(contract.get(field), list):
            errors.append(f"{field} must be an array")

    evidence = contract.get("evidence") if isinstance(contract.get("evidence"), list) else []
    for index, item in enumerate(evidence):
        prefix = f"evidence[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        expected = {"claim", "source_type", "source", "locator", "checked_at"}
        if set(item) != expected:
            errors.append(f"{prefix} fields must be exactly {sorted(expected)}")
        for field in ("claim", "source", "locator"):
            _nonempty_string(item.get(field), f"{prefix}.{field}", errors)
        source_type = item.get("source_type")
        if source_type not in SOURCE_TYPES:
            errors.append(f"{prefix}.source_type must be one of {sorted(SOURCE_TYPES)}")
        _validate_checked_at(item.get("checked_at"), f"{prefix}.checked_at", errors)

        source = item.get("source")
        locator = item.get("locator")
        if source_type == "local_file" and isinstance(source, str) and source.strip():
            _validate_local_file(source.strip(), repo_root, f"{prefix}.source", errors)
        elif source_type == "official_web" and isinstance(source, str):
            parsed = urlparse(source.strip())
            if parsed.scheme != "https" or not parsed.netloc:
                errors.append(f"{prefix}.source must be an HTTPS URL")
            if item.get("checked_at") is None:
                errors.append(f"{prefix}.checked_at is required for official_web")
        elif source_type == "calculation" and (not isinstance(locator, str) or not locator.strip()):
            errors.append(f"{prefix}.locator must contain a reproducible formula")

    object_arrays = {
        "assumptions": {"statement", "impact", "verification"},
        "critical_gaps": {"missing", "impact", "request"},
        "conflicts": {"sources", "issue", "impact"},
    }
    for field, expected in object_arrays.items():
        items = contract.get(field) if isinstance(contract.get(field), list) else []
        for index, item in enumerate(items):
            prefix = f"{field}[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{prefix} must be an object")
                continue
            if set(item) != expected:
                errors.append(f"{prefix} fields must be exactly {sorted(expected)}")
            for key in expected - {"sources"}:
                _nonempty_string(item.get(key), f"{prefix}.{key}", errors)
            if field == "conflicts":
                sources = item.get("sources")
                if not isinstance(sources, list) or not sources:
                    errors.append(f"{prefix}.sources must be a non-empty array")
                elif any(not isinstance(value, str) or not value.strip() for value in sources):
                    errors.append(f"{prefix}.sources entries must be non-empty strings")

    boundaries = contract.get("boundaries") if isinstance(contract.get("boundaries"), list) else []
    if not boundaries or any(not isinstance(item, str) or not item.strip() for item in boundaries):
        errors.append("boundaries must contain at least one non-empty string")

    assumptions = contract.get("assumptions") if isinstance(contract.get("assumptions"), list) else []
    gaps = contract.get("critical_gaps") if isinstance(contract.get("critical_gaps"), list) else []
    conflicts = contract.get("conflicts") if isinstance(contract.get("conflicts"), list) else []
    question = contract.get("minimal_question")

    if status == "VERIFIED":
        if not evidence:
            errors.append("VERIFIED requires non-empty evidence")
        if assumptions or gaps or conflicts:
            errors.append("VERIFIED forbids assumptions, critical_gaps, and unresolved conflicts")
        if question is not None:
            errors.append("VERIFIED requires minimal_question = null")
    elif status == "CONDITIONAL":
        if not evidence or not assumptions:
            errors.append("CONDITIONAL requires non-empty evidence and assumptions")
        if gaps or conflicts:
            errors.append("CONDITIONAL forbids critical_gaps and unresolved conflicts")
        if question is not None:
            errors.append("CONDITIONAL requires minimal_question = null")
    elif status == "NEEDS_INPUT":
        if not gaps and not conflicts:
            errors.append("NEEDS_INPUT requires critical_gaps or unresolved conflicts")
        if not isinstance(question, str) or not question.strip():
            errors.append("NEEDS_INPUT requires one non-empty minimal_question")
        elif question.count("?") != 1 or not question.rstrip().endswith("?"):
            errors.append("minimal_question must contain exactly one question")

    return errors


def validate_message(message: str, repo_root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate a complete subagent message and return parsed contract and errors."""
    try:
        contract, remainder = extract_contract(message)
    except ContractError as exc:
        return None, [str(exc)]
    errors = validate_contract(contract, repo_root)
    if contract.get("status") == "NEEDS_INPUT" and FINAL_DECISION_RE.search(remainder):
        errors.append("NEEDS_INPUT response must not contain a final recommendation, verdict, or decision")
    return contract, errors


def rejection_payload(errors: list[str], stop_hook_active: bool) -> dict[str, Any]:
    """Create a valid Codex hook response without allowing an infinite retry loop."""
    detail = "; ".join(errors[:8])
    if len(errors) > 8:
        detail += f"; and {len(errors) - 8} more error(s)"
    reason = f"Epistemic contract rejected: {detail}"
    if stop_hook_active:
        return {
            "continue": False,
            "stopReason": reason,
            "systemMessage": "Repeated invalid specialist result was stopped. Treat it as rejected.",
        }
    return {"decision": "block", "reason": reason}
