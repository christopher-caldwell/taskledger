#!/usr/bin/env python3
"""Opt-in live behavioral evaluation for the explicit $taskledger workflow."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "taskledger_skill" / "fixtures"
INSTALLED_SKILL = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills" / "taskledger" / "SKILL.md"
REVISION_PROMPTS = {
    "01-routine": "Revise the plan so the helper and its focused tests are separate tasks. This is not approval.",
    "02-complex": "Revise the plan so the persistence boundary is established before API migration. This is not approval.",
    "03-parallel": "Revise the plan to make the independence and write surfaces explicit. This is not approval.",
    "04-overlap": "Revise the plan so only one task owns src/shared.py. This is not approval.",
    "05-ambiguity": "Use seven days as the required default, revise the complete plan, and ask for approval again. This is not approval.",
    "06-existing": "Revise the plan to include a compatibility acceptance criterion. This is not approval.",
}


def run(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stderr[-4000:]}\n{result.stdout[-4000:]}")
    return result


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def json_events(output: str) -> list[dict[str, Any]]:
    events = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def find_value(value: Any, names: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in names and isinstance(item, str) and item:
                return item
        for item in value.values():
            found = find_value(item, names)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_value(item, names)
            if found:
                return found
    return None


def session_id(events: list[dict[str, Any]]) -> str:
    for event in events:
        kind = str(event.get("type", "")).lower()
        if "thread" in kind or "session" in kind:
            found = find_value(event, {"thread_id", "threadId", "session_id", "sessionId"})
            if found:
                return found
    raise AssertionError("Codex JSONL did not expose a session/thread id")


def final_text(events: list[dict[str, Any]]) -> str:
    candidates: list[str] = []
    for event in events:
        kind = str(event.get("type", "")).lower()
        if "message" in kind or "item.completed" in kind:
            found = find_value(event, {"text", "final_response", "output_text"})
            if found:
                candidates.append(found)
    if not candidates:
        raise AssertionError("Codex JSONL did not contain an assistant message")
    return candidates[-1]


def assert_waiting_for_approval(repo: Path, text: str) -> None:
    required = ("Approve this plan?", "Requirements", "Wave", "Acceptance", "Checks", "Write surfaces", "sha256:")
    missing = [item for item in required if item not in text]
    if missing:
        raise AssertionError(f"approval response omitted {missing}:\n{text}")
    database = repo / ".taskledger" / "taskledger.sqlite3"
    if not database.is_file():
        raise AssertionError("LOAD did not initialize Taskledger")
    con = sqlite3.connect(database)
    try:
        for table in ("project_preparations", "tasks", "assignments", "controller_runs", "controller_sessions"):
            if con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                raise AssertionError(f"{table} changed before approval")
    finally:
        con.close()


def assert_committed(repo: Path, text: str) -> None:
    if "taskledger start" not in text or "taskledger ui" not in text:
        raise AssertionError(f"final handoff omitted start/ui commands:\n{text}")
    con = sqlite3.connect(repo / ".taskledger" / "taskledger.sqlite3")
    try:
        row = con.execute("SELECT state,origin FROM project_preparations ORDER BY created_at DESC LIMIT 1").fetchone()
        if row != ("AWAITING_APPROVAL", "INVOKING_MODEL"):
            raise AssertionError(f"unexpected committed preparation: {row}")
        for table in ("tasks", "assignments", "controller_runs", "controller_sessions"):
            if con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                raise AssertionError(f"{table} was created during plan commit")
    finally:
        con.close()


def evaluate_fixture(fixture: Path, *, model: str | None, thinking: str, timeout: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="taskledger-skill-eval-") as directory:
        repo = Path(directory) / "repo"
        shutil.copytree(fixture, repo)
        git(repo, "init", "-q", "-b", "feat/eval")
        git(repo, "config", "user.name", "Taskledger Eval")
        git(repo, "config", "user.email", "taskledger-eval@example.invalid")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "Fixture baseline")

        shim = Path(directory) / "bin"
        shim.mkdir()
        executable = shim / "taskledger"
        executable.write_text(f"#!/bin/sh\nexec {sys.executable!s} -m taskledger \"$@\"\n")
        executable.chmod(0o755)
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PATH": str(shim) + os.pathsep + os.environ.get("PATH", "")}
        base = [
            "codex", "-a", "never", "-s", "workspace-write",
            "-c", f"model_reasoning_effort={json.dumps(thinking)}",
        ]
        if model:
            base += ["-m", model]
        first = run(base + ["-C", str(repo), "exec", "--json", "Use $taskledger to prepare ./spec.md. Do not implement or start execution."], cwd=repo, env=env, timeout=timeout)
        first_events = json_events(first.stdout)
        thread = session_id(first_events)
        assert_waiting_for_approval(repo, final_text(first_events))
        try:
            revised = run(base + ["exec", "resume", "--json", thread, REVISION_PROMPTS[fixture.name]], cwd=repo, env=env, timeout=timeout)
            assert_waiting_for_approval(repo, final_text(json_events(revised.stdout)))
            approved = run(base + ["exec", "resume", "--json", thread, "Yes, I approve the exact current plan and hash."], cwd=repo, env=env, timeout=timeout)
            assert_committed(repo, final_text(json_events(approved.stdout)))
        finally:
            subprocess.run(["codex", "delete", "--force", thread], cwd=repo, env=env, text=True, capture_output=True)
        return {"fixture": fixture.name, "status": "PASS"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--model")
    parser.add_argument("--thinking", choices=("minimal", "low", "medium", "high", "xhigh"), default="low")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    source = ROOT / "skills" / "taskledger" / "SKILL.md"
    if not INSTALLED_SKILL.is_file() or INSTALLED_SKILL.read_bytes() != source.read_bytes():
        raise SystemExit("Install the repository skill before evaluation; the installed SKILL.md does not match this checkout.")
    results = []
    for repetition in range(1, args.repetitions + 1):
        for fixture in sorted(path for path in FIXTURES.iterdir() if path.is_dir()):
            result = evaluate_fixture(fixture, model=args.model, thinking=args.thinking, timeout=args.timeout)
            result["repetition"] = repetition
            results.append(result)
            print(json.dumps(result, sort_keys=True), flush=True)
    print(json.dumps({"status": "PASS", "runs": len(results)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
