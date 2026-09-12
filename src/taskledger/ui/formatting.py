from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from taskledger.application.contracts import Record


_control = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
    r"|\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)?)"
)


def safe_text(value: object) -> str:
    """Render untrusted ledger text without terminal controls or Rich markup."""
    return _control.sub("", str(value)).replace("[", "\\[")


def as_mapping(value: Any) -> dict[str, Any]:
    """Convert public Record/nested tuple-pair values without domain imports."""
    if isinstance(value, Record):
        value = value.fields
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (tuple, list)) and all(
        isinstance(item, (tuple, list)) and len(item) == 2 for item in value
    ):
        return {str(key): item for key, item in value}
    return {}


def nested(value: Any, *path: str, default: Any = None) -> Any:
    current = value
    for name in path:
        current = as_mapping(current).get(name, default)
        if current is default:
            break
    converted = as_mapping(current)
    return converted if converted else current


def optional(value: Any, fallback: str = "—") -> str:
    return fallback if value is None or value == "" else safe_text(value)


def integer(value: Any, fallback: str = "—") -> str:
    if value is None:
        return fallback
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return safe_text(value)


def token_count(value: Any) -> str:
    if value is None:
        return "Unknown"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return safe_text(value)
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.1f}m"
    if abs(number) >= 1_000:
        return f"{number / 1_000:.1f}k"
    return f"{number:,}"


def short_id(value: Any, length: int = 8) -> str:
    text = optional(value)
    return text if text == "—" or len(text) <= length else f"{text[:length]}…"


def repository_name(value: Any) -> str:
    if not value:
        return "No repository"
    return safe_text(Path(str(value)).name or str(value))


def known_tokens(usage: Record) -> Any:
    for name in ("known_token_subtotal", "known_total_tokens", "known_tokens", "total_tokens"):
        value = usage.get(name)
        if value is not None:
            return value
    return None


def task_activity(task: Record) -> str:
    if (task.get("blocker_count") or 0) > 0:
        return "Blocked"
    state = task.get("state")
    if state == "PLANNED":
        return "Queued" if task.get("assignment_id") else "Waiting"
    return {
        "ASSIGNED": "Implementing",
        "SUBMITTED": "Review",
        "ACCEPTED": "Integration",
        "COMPLETED": "Integrated" if task.get("integrated") else "Completed",
        "CANCELLED": "Cancelled",
    }.get(state, optional(state))


def session_activity(session: Record) -> str:
    state = session.get("state")
    turn = session.get("latest_turn_state")
    if state == "UNCERTAIN" or turn == "UNCERTAIN":
        return "Uncertain"
    if state == "CLOSED":
        return "Closed"
    if state == "ACTIVE" and turn == "RUNNING":
        return "Active turn"
    if state == "ACTIVE":
        return "Retained"
    return optional(state)
