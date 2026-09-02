#!/usr/bin/env python3
"""Static regression checks for the epistemic-integrity configuration."""

from __future__ import annotations

import json
import hashlib
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover - explicit environment failure
    raise SystemExit("Python 3.11+ is required (tomllib is unavailable).") from exc


ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = ROOT / ".codex" / "agents"
CASES_PATH = Path(__file__).with_name("cases.jsonl")

EXPECTED_AGENT_FILES = {
    "authors-arranger-producer.toml",
    "authors-composer.toml",
    "authors-lyricist.toml",
    "authors-lyrics-reviewer.toml",
    "authors-music-reviewer.toml",
    "authors-production-reviewer.toml",
    "executive-finance-tax.toml",
    "executive-hrbp.toml",
    "executive-lawyer.toml",
    "executive-management-controller.toml",
    "executive-property-operations.toml",
}

READ_ONLY_REVIEWERS = {
    "authors-lyrics-reviewer.toml",
    "authors-music-reviewer.toml",
    "authors-production-reviewer.toml",
    "executive-management-controller.toml",
}

CONTRACT_MARKERS = [
    "<EPISTEMIC_CONTRACT>",
    "jarvis.epistemic.v1",
    "`schema`",
    "`status`",
    "`evidence`",
    "`assumptions`",
    "`critical_gaps`",
    "`conflicts`",
    "`boundaries`",
    "`minimal_question`",
]

ORCHESTRATORS = [
    ROOT / "agents" / "executive-director" / "AGENTS.md",
    ROOT / "agents" / "authors-workshop" / "AGENTS.md",
]

WORKFLOWS = [
    ROOT / "agents" / "executive-director" / ".agents" / "skills" / "finance-tax-workflow" / "SKILL.md",
    ROOT / "agents" / "executive-director" / ".agents" / "skills" / "hr-business-partner" / "SKILL.md",
    ROOT / "agents" / "executive-director" / ".agents" / "skills" / "legal-workflow" / "SKILL.md",
    ROOT / "agents" / "executive-director" / ".agents" / "skills" / "property-operations" / "SKILL.md",
    ROOT / "agents" / "authors-workshop" / ".agents" / "skills" / "authors-song-workflow" / "SKILL.md",
]


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"cannot read {path.relative_to(ROOT)}: {exc}")
        return ""


def require(text: str, needle: str, location: str) -> None:
    if needle not in text:
        errors.append(f"{location}: missing {needle!r}")


errors: list[str] = []

root_agents = read(ROOT / "AGENTS.md")
standard = read(ROOT / "knowledge" / "epistemic-integrity-standard.md")
for marker in ("## Протокол достоверности", "NEEDS_INPUT", "CONDITIONAL", "VERIFIED"):
    require(root_agents, marker, "AGENTS.md")
for marker in (
    "## 1. Категории утверждений",
    "## 3. Ворота решения",
    "## 5. Машиночитаемый контракт результата custom agent",
    "## 6. Исполняемая проверка передачи",
    "## 7. Пути и обязательные инструкции",
):
    require(standard, marker, "knowledge/epistemic-integrity-standard.md")

claude = read(ROOT / "CLAUDE.md")
require(claude, "Не загружай их автоматически", "CLAUDE.md")
if "Перед началом каждой сессии (обязательно)" in claude:
    errors.append("CLAUDE.md: still mandates automatic loading of all personal context")

actual_files = {path.name for path in AGENTS_DIR.glob("*.toml")}
if actual_files != EXPECTED_AGENT_FILES:
    missing = sorted(EXPECTED_AGENT_FILES - actual_files)
    extra = sorted(actual_files - EXPECTED_AGENT_FILES)
    errors.append(f"custom-agent set mismatch; missing={missing}, extra={extra}")

agent_names: set[str] = set()
raw_cwd_path = re.compile(r"(?<!<repo-root>/)agents/(?:executive-director|authors-workshop)/")
for path in sorted(AGENTS_DIR.glob("*.toml")):
    location = str(path.relative_to(ROOT))
    source = read(path)
    try:
        data = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        errors.append(f"{location}: invalid TOML: {exc}")
        continue

    for field in ("name", "description", "developer_instructions"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            errors.append(f"{location}: required non-empty string {field!r}")

    name = data.get("name")
    if isinstance(name, str):
        if name in agent_names:
            errors.append(f"{location}: duplicate agent name {name!r}")
        agent_names.add(name)

    instructions = data.get("developer_instructions", "")
    for marker in CONTRACT_MARKERS:
        require(instructions, marker, location)
    if instructions.count("<EPISTEMIC_CONTRACT>") != 1:
        errors.append(f"{location}: must describe exactly one machine contract")
    if "Статус достоверности: VERIFIED | CONDITIONAL | NEEDS_INPUT" in instructions:
        errors.append(f"{location}: legacy free-text contract remains")

    for marker in (
        "<repo-root>/knowledge/epistemic-integrity-standard.md",
        "git rev-parse --show-toplevel",
        "корневой `AGENTS.md` и `.codex/`",
        "Если обязательный файл недоступен, не делай вид, что прочитал его.",
        "При `NEEDS_INPUT` задай в `minimal_question` ровно один минимальный вопрос",
    ):
        require(instructions, marker, location)

    if raw_cwd_path.search(instructions):
        errors.append(f"{location}: contains a CWD-dependent professional-context path")

    if path.name in READ_ONLY_REVIEWERS and data.get("sandbox_mode") != "read-only":
        errors.append(f"{location}: reviewer must use sandbox_mode = 'read-only'")

for path in ORCHESTRATORS:
    location = str(path.relative_to(ROOT))
    text = read(path)
    for marker in (
        "### Приёмка результата специалиста",
        "<EPISTEMIC_CONTRACT>",
        "SubagentStop",
        "VERIFIED",
        "CONDITIONAL",
        "NEEDS_INPUT",
        "не усредняй",
    ):
        require(text.casefold() if marker == "не усредняй" else text, marker, location)

for path in WORKFLOWS:
    location = str(path.relative_to(ROOT))
    text = read(path)
    require(text, "<repo-root>/knowledge/epistemic-integrity-standard.md", location)
    require(text, "<EPISTEMIC_CONTRACT>", location)
    require(text, "jarvis.epistemic.v1", location)

config_path = ROOT / ".codex" / "config.toml"
config_source = read(config_path)
try:
    config = tomllib.loads(config_source)
except tomllib.TOMLDecodeError as exc:
    errors.append(f".codex/config.toml: invalid TOML: {exc}")
    config = {}
if config.get("features", {}).get("hooks") is not True:
    errors.append(".codex/config.toml: features.hooks must be true")

hooks_path = ROOT / ".codex" / "hooks.json"
try:
    hooks_config = json.loads(read(hooks_path))
except json.JSONDecodeError as exc:
    errors.append(f".codex/hooks.json: invalid JSON: {exc}")
    hooks_config = {}
subagent_hooks = hooks_config.get("hooks", {}).get("SubagentStop", [])
if not isinstance(subagent_hooks, list) or len(subagent_hooks) != 1:
    errors.append(".codex/hooks.json: exactly one SubagentStop group is required")
else:
    group = subagent_hooks[0]
    matcher = group.get("matcher") if isinstance(group, dict) else None
    try:
        matcher_re = re.compile(matcher) if isinstance(matcher, str) else None
    except re.error as exc:
        errors.append(f".codex/hooks.json: invalid matcher: {exc}")
        matcher_re = None
    if matcher_re is not None:
        unmatched = sorted(name for name in agent_names if matcher_re.fullmatch(name) is None)
        if unmatched:
            errors.append(f".codex/hooks.json: matcher misses custom agents {unmatched}")
    handlers = group.get("hooks", []) if isinstance(group, dict) else []
    if not isinstance(handlers, list) or len(handlers) != 1:
        errors.append(".codex/hooks.json: exactly one SubagentStop handler is required")
    else:
        handler = handlers[0]
        for key in ("command", "commandWindows"):
            if not isinstance(handler.get(key), str) or "git rev-parse --show-toplevel" not in handler[key]:
                errors.append(f".codex/hooks.json: {key} must resolve the repository root")

hook_path = ROOT / ".codex" / "hooks" / "validate_epistemic_contract.py"
hook_source_path = Path(__file__).with_name("hook_entrypoint.py")
hook_source = read(hook_path)
reviewed_hook_source = read(hook_source_path)
if hook_source != reviewed_hook_source:
    errors.append("runtime hook differs from its reviewed source in evals/epistemic-integrity")
validator_path = Path(__file__).with_name("contract_validation.py")
validator_hash = hashlib.sha256(validator_path.read_bytes()).hexdigest()
if f'VALIDATOR_SHA256 = "{validator_hash}"' not in reviewed_hook_source:
    errors.append("hook does not pin the current contract validator SHA-256")
for required_eval_file in (
    "contract_validation.py",
    "hook_entrypoint.py",
    "run_behavioral_eval.py",
    "test_contract_validation.py",
    "test_hook_integration.py",
    "test_behavioral_runner.py",
):
    if not Path(__file__).with_name(required_eval_file).is_file():
        errors.append(f"missing executable eval component: {required_eval_file}")

cases: list[dict[str, object]] = []
case_ids: set[str] = set()
try:
    case_lines = CASES_PATH.read_text(encoding="utf-8").splitlines()
except OSError as exc:
    errors.append(f"cannot read eval cases: {exc}")
    case_lines = []

for line_number, line in enumerate(case_lines, start=1):
    if not line.strip():
        continue
    try:
        case = json.loads(line)
    except json.JSONDecodeError as exc:
        errors.append(f"cases.jsonl:{line_number}: invalid JSON: {exc}")
        continue
    cases.append(case)
    case_id = case.get("id")
    if not isinstance(case_id, str) or not case_id:
        errors.append(f"cases.jsonl:{line_number}: invalid id")
    elif case_id in case_ids:
        errors.append(f"cases.jsonl:{line_number}: duplicate id {case_id!r}")
    else:
        case_ids.add(case_id)
    if case.get("expected_status") not in {"VERIFIED", "CONDITIONAL", "NEEDS_INPUT"}:
        errors.append(f"cases.jsonl:{line_number}: invalid expected_status")
    for field in ("prompt", "scope", "target_agent", "risk"):
        if not isinstance(case.get(field), str) or not case[field]:
            errors.append(f"cases.jsonl:{line_number}: missing string {field!r}")
    for field in ("fixtures", "must", "must_not"):
        if not isinstance(case.get(field), list):
            errors.append(f"cases.jsonl:{line_number}: {field!r} must be a list")

scopes = {case.get("scope") for case in cases}
statuses = {case.get("expected_status") for case in cases}
if scopes != {"personal", "executive-director", "authors-workshop"}:
    errors.append(f"eval scope coverage is incomplete: {sorted(str(value) for value in scopes)}")
if statuses != {"VERIFIED", "CONDITIONAL", "NEEDS_INPUT"}:
    errors.append(f"eval status coverage is incomplete: {sorted(str(value) for value in statuses)}")
if len(cases) < 15:
    errors.append(f"eval set is too small: {len(cases)} cases (minimum 15)")

if errors:
    print("FAIL: epistemic-integrity configuration")
    for error in errors:
        print(f"- {error}")
    raise SystemExit(1)

print(
    "PASS: epistemic-integrity configuration "
    f"({len(actual_files)} custom agents, {len(cases)} behavioral cases)"
)
