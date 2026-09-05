#!/usr/bin/env python3
"""Capture task-level complexity and token usage from a Taskledger run.

The output deliberately keeps the observable dimensions separate.  It does not
collapse lines changed, criteria, rejections, and elapsed time into an opaque
"complexity score".
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--ledger-db", required=True, type=Path)
    parser.add_argument("--sessions", type=Path, default=Path.home() / ".codex" / "sessions")
    parser.add_argument("--task-id", action="append", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def session_metadata(path: Path) -> dict | None:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("type") == "session_meta":
                return item.get("payload", {})
    return None


def inherited_history_cutoff(path: Path, metadata: dict) -> datetime | None:
    """Return the end of copied startup history in a forked rollout."""
    if not metadata.get("forked_from_id"):
        return None
    startup_deadline = timestamp(metadata["timestamp"]).timestamp() + 5
    cutoff = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw_time = item.get("timestamp")
            if not raw_time:
                continue
            event_time = timestamp(raw_time)
            if event_time.timestamp() > startup_deadline:
                break
            if item.get("payload", {}).get("type") == "thread_settings_applied":
                cutoff = event_time
    return cutoff


def belongs_to_repo(cwd: str | None, repo: Path) -> bool:
    if not cwd:
        return False
    try:
        Path(cwd).resolve().relative_to(repo)
        return True
    except (OSError, ValueError):
        return False


def git_diff(repo: Path, before: str, after: str) -> dict:
    completed = subprocess.run(
        ["git", "diff", "--numstat", before, after],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )
    additions = deletions = binary_files = 0
    files = []
    for line in completed.stdout.splitlines():
        added, deleted, name = line.split("\t", 2)
        files.append(name)
        if added == "-" or deleted == "-":
            binary_files += 1
        else:
            additions += int(added)
            deletions += int(deleted)
    return {
        "files_changed": len(files),
        "additions": additions,
        "deletions": deletions,
        "lines_changed": additions + deletions,
        "binary_files": binary_files,
        "top_level_areas": dict(Counter(name.split("/", 1)[0] for name in files)),
    }


def ledger_tasks(database: Path, task_ids: list[str]) -> list[dict]:
    connection = sqlite3.connect(database.as_uri() + "?immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in task_ids)
    query = f"""
        WITH criteria AS (
          SELECT task_id,task_revision,COUNT(*) AS count
          FROM task_acceptance_criteria GROUP BY task_id,task_revision
        ), requirements AS (
          SELECT task_id,task_revision,COUNT(*) AS count
          FROM task_requirement_links GROUP BY task_id,task_revision
        ), dependencies AS (
          SELECT task_id,task_revision,COUNT(*) AS count
          FROM task_dependencies GROUP BY task_id,task_revision
        ), assignment_metrics AS (
          SELECT task_id,COUNT(*) AS attempts,MIN(activated_at) AS activated_at
          FROM assignments GROUP BY task_id
        ), submission_metrics AS (
          SELECT a.task_id,COUNT(DISTINCT s.id) AS submissions,
                 SUM(CASE WHEN sv.outcome='ACCEPTED' THEN 1 ELSE 0 END) AS accepted,
                 SUM(CASE WHEN sv.outcome='REJECTED' THEN 1 ELSE 0 END) AS rejected,
                 SUM(CASE WHEN sv.outcome='BLOCKED' THEN 1 ELSE 0 END) AS blocked
          FROM assignments a
          LEFT JOIN submissions s ON s.assignment_id=a.id
          LEFT JOIN submission_verifications sv ON sv.submission_id=s.id
          GROUP BY a.task_id
        ), successful_integration AS (
          SELECT ia.* FROM integration_attempts ia
          JOIN (
            SELECT task_id,MAX(finished_at) AS finished_at
            FROM integration_attempts WHERE state='SUCCEEDED' GROUP BY task_id
          ) latest ON latest.task_id=ia.task_id AND latest.finished_at=ia.finished_at
          WHERE ia.state='SUCCEEDED'
        )
        SELECT t.id,tr.objective,tr.implementation_scope,t.created_at,
               am.activated_at,si.finished_at AS integrated_at,
               si.canonical_before_oid,si.canonical_after_oid,
               COALESCE(c.count,0) AS acceptance_criteria,
               COALESCE(r.count,0) AS linked_requirements,
               COALESCE(d.count,0) AS dependencies,
               COALESCE(am.attempts,0) AS assignment_attempts,
               COALESCE(sm.submissions,0) AS submissions,
               COALESCE(sm.accepted,0) AS accepted_reviews,
               COALESCE(sm.rejected,0) AS rejected_reviews,
               COALESCE(sm.blocked,0) AS blocked_reviews
        FROM tasks t
        JOIN task_revisions tr ON tr.task_id=t.id AND tr.revision=t.current_revision
        LEFT JOIN criteria c ON c.task_id=t.id AND c.task_revision=t.current_revision
        LEFT JOIN requirements r ON r.task_id=t.id AND r.task_revision=t.current_revision
        LEFT JOIN dependencies d ON d.task_id=t.id AND d.task_revision=t.current_revision
        LEFT JOIN assignment_metrics am ON am.task_id=t.id
        LEFT JOIN submission_metrics sm ON sm.task_id=t.id
        LEFT JOIN successful_integration si ON si.task_id=t.id
        WHERE t.id IN ({placeholders})
        ORDER BY si.finished_at
    """
    try:
        rows = [dict(row) for row in connection.execute(query, task_ids)]
    finally:
        connection.close()
    found = {row["id"] for row in rows}
    missing = sorted(set(task_ids) - found)
    if missing:
        raise SystemExit(f"task IDs not found: {', '.join(missing)}")
    for row in rows:
        if not row["activated_at"] or not row["integrated_at"]:
            raise SystemExit(f"task is missing a complete execution window: {row['id']}")
        start = timestamp(row["activated_at"])
        end = timestamp(row["integrated_at"])
        row["execution_seconds"] = round((end - start).total_seconds(), 3)
    return rows


def add_session_usage(tasks: list[dict], sessions: Path, repo: Path) -> dict:
    windows = [(timestamp(task["activated_at"]), timestamp(task["integrated_at"]), task) for task in tasks]
    overlap_pairs = []
    for index, (start, end, task) in enumerate(windows):
        for other_start, other_end, other in windows[index + 1 :]:
            if start < other_end and other_start < end:
                overlap_pairs.append([task["id"], other["id"]])

    buckets: dict[str, dict[str, dict]] = {
        task["id"]: defaultdict(lambda: {"token_events": 0, "compactions": 0, "tokens": Counter()})
        for task in tasks
    }
    files_scanned = files_matched = 0
    inherited_history_files = 0
    inherited_token_events_excluded = 0
    inherited_compactions_excluded = 0
    for path in sessions.rglob("*.jsonl"):
        files_scanned += 1
        metadata = session_metadata(path)
        if not metadata or not belongs_to_repo(metadata.get("cwd"), repo):
            continue
        files_matched += 1
        inherited_cutoff = inherited_history_cutoff(path, metadata)
        if inherited_cutoff:
            inherited_history_files += 1
        model = effort = "unknown"
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                raw_time = item.get("timestamp")
                if not raw_time:
                    continue
                event_time = timestamp(raw_time)
                payload = item.get("payload", {})
                if item.get("type") == "turn_context":
                    model = payload.get("model", model)
                    settings = payload.get("collaboration_mode", {}).get("settings", {})
                    effort = payload.get("effort", settings.get("reasoning_effort", effort))
                elif item.get("type") == "world_state":
                    state = payload.get("state", {})
                    model = state.get("model", model)
                    effort = state.get("model_reasoning_effort", state.get("reasoning_effort", effort))
                elif payload.get("type") == "thread_settings_applied":
                    settings = payload.get("thread_settings", {})
                    model = settings.get("model", model)
                    effort = settings.get("model_reasoning_effort", settings.get("reasoning_effort", effort))
                elif inherited_cutoff and event_time <= inherited_cutoff:
                    if payload.get("type") == "token_count":
                        inherited_token_events_excluded += 1
                    elif payload.get("type") == "context_compacted":
                        inherited_compactions_excluded += 1
                    continue
                cohort = f"{model}|{effort}"
                for start, end, task in windows:
                    if not start <= event_time <= end:
                        continue
                    bucket = buckets[task["id"]][cohort]
                    if payload.get("type") == "token_count":
                        usage = payload.get("info", {}).get("last_token_usage", {})
                        if usage:
                            bucket["token_events"] += 1
                            for field in TOKEN_FIELDS:
                                bucket["tokens"][field] += int(usage.get(field, 0) or 0)
                    if payload.get("type") == "context_compacted":
                        bucket["compactions"] += 1

    for task in tasks:
        usage = {}
        for cohort, bucket in sorted(buckets[task["id"]].items()):
            if bucket["token_events"] or bucket["compactions"]:
                usage[cohort] = {
                    "token_events": bucket["token_events"],
                    "compactions": bucket["compactions"],
                    "tokens": dict(bucket["tokens"]),
                }
        task["usage_by_model_effort"] = usage
    return {
        "sessions": str(sessions),
        "files_scanned": files_scanned,
        "files_matched_to_repo": files_matched,
        "inherited_history": {
            "forked_files": inherited_history_files,
            "token_events_excluded": inherited_token_events_excluded,
            "compactions_excluded": inherited_compactions_excluded,
        },
        "overlapping_task_windows": overlap_pairs,
    }


def main() -> None:
    args = arguments()
    repo = args.repo.resolve()
    tasks = ledger_tasks(args.ledger_db.resolve(), args.task_id)
    for task in tasks:
        task["git_diff"] = git_diff(repo, task["canonical_before_oid"], task["canonical_after_oid"])
    source = add_session_usage(tasks, args.sessions.resolve(), repo)
    result = {
        "schema_version": 1,
        "label": args.label,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "repository": str(repo),
        "ledger_db": str(args.ledger_db.resolve()),
        "source": source,
        "tasks": tasks,
        "interpretation": {
            "use": "Compare like task families and inspect each observable complexity dimension before aggregating tokens.",
            "do_not_use": "Do not treat elapsed time, lines changed, or integrated-task count alone as task complexity.",
            "planning": "Planning tokens fall outside assignment-to-integration windows and must be reported separately.",
            "overlap": "If task windows overlap, their token totals are not additive and must not be pooled.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
