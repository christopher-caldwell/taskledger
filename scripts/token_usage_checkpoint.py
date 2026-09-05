#!/usr/bin/env python3
"""Create a privacy-safe, model-stratified Codex usage checkpoint."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


COMMAND = re.compile(
    r"(?:^|[\s\"'])taskledger\s+"
    r"(project|spec|requirement|task|plan|assignment|worker|submission|blocker|question|follow-up|operation)\s+"
    r"([a-z][a-z-]*)"
)
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
    parser.add_argument("--sessions", type=Path, default=Path.home() / ".codex" / "sessions")
    parser.add_argument("--since", required=True, help="Inclusive ISO-8601 timestamp")
    parser.add_argument("--until", required=True, help="Exclusive ISO-8601 timestamp")
    parser.add_argument("--label", required=True)
    parser.add_argument("--taskledger-version", required=True)
    parser.add_argument("--session-id", help="Restrict to one root session and its linked subagents/reviewers")
    parser.add_argument("--ledger-db", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def normalized_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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


def inherited_history_cutoff(path: Path, metadata: dict) -> str | None:
    """Return the end of a forked session's copied startup history.

    Codex full-history forks serialize the parent's historical events into the
    child rollout at the child's creation timestamp.  Those copied events
    include token_count and context_compacted records and must not be counted as
    fresh child usage.  The child's own settings are applied at the end of that
    startup cluster.
    """
    if not metadata.get("forked_from_id"):
        return None
    started_at = normalized_timestamp(metadata["timestamp"])
    startup_deadline = datetime.fromtimestamp(
        datetime.fromisoformat(started_at.replace("Z", "+00:00")).timestamp() + 5,
        tz=timezone.utc,
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    cutoff = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = item.get("timestamp")
            if not timestamp:
                continue
            timestamp = normalized_timestamp(timestamp)
            if timestamp > startup_deadline:
                break
            payload = item.get("payload", {})
            if payload.get("type") == "thread_settings_applied":
                cutoff = timestamp
    return cutoff


def belongs_to_repo(cwd: str | None, repo: Path) -> bool:
    if not cwd:
        return False
    try:
        Path(cwd).resolve().relative_to(repo)
        return True
    except (OSError, ValueError):
        return False


def ledger_events(database: Path | None, repo: Path, since: str, until: str) -> dict:
    if not database or not database.is_file():
        return {"available": False, "reason": "ledger database not supplied or unavailable"}
    connection = sqlite3.connect(database.as_uri() + "?immutable=1", uri=True)
    try:
        project = connection.execute(
            "SELECT id FROM projects WHERE repository_root=?", (str(repo),)
        ).fetchone()
        if not project:
            return {"available": False, "reason": "repository not found in ledger"}
        rows = connection.execute(
            "SELECT event_type,COUNT(*) FROM audit_events "
            "WHERE project_id=? AND created_at>=? AND created_at<? GROUP BY event_type ORDER BY event_type",
            (project[0], since, until),
        )
        event_counts = dict(rows)
        planning_queries = {
            "specifications_created": (
                "SELECT COUNT(*) FROM specifications WHERE project_id=? AND created_at>=? AND created_at<?"
            ),
            "specification_revisions_observed": (
                "SELECT COUNT(*) FROM specification_revisions sr JOIN specifications s ON s.id=sr.specification_id "
                "WHERE s.project_id=? AND sr.observed_at>=? AND sr.observed_at<?"
            ),
            "specification_revisions_approved": (
                "SELECT COUNT(*) FROM specification_revisions sr JOIN specifications s ON s.id=sr.specification_id "
                "WHERE s.project_id=? AND sr.approved_at>=? AND sr.approved_at<?"
            ),
            "requirements_created": (
                "SELECT COUNT(*) FROM requirements WHERE project_id=? AND created_at>=? AND created_at<?"
            ),
            "tasks_created": (
                "SELECT COUNT(*) FROM tasks WHERE project_id=? AND created_at>=? AND created_at<?"
            ),
            "acceptance_criteria_created": (
                "SELECT COUNT(*) FROM task_acceptance_criteria c JOIN tasks t ON t.id=c.task_id "
                "WHERE t.project_id=? AND t.created_at>=? AND t.created_at<?"
            ),
            "task_requirement_links_created": (
                "SELECT COUNT(*) FROM task_requirement_links l JOIN tasks t ON t.id=l.task_id "
                "WHERE t.project_id=? AND t.created_at>=? AND t.created_at<?"
            ),
            "task_dependencies_created": (
                "SELECT COUNT(*) FROM task_dependencies d JOIN tasks t ON t.id=d.task_id "
                "WHERE t.project_id=? AND t.created_at>=? AND t.created_at<?"
            ),
            "specification_reviews_created": (
                "SELECT COUNT(*) FROM specification_reviews WHERE project_id=? AND created_at>=? AND created_at<?"
            ),
            "plan_validations": (
                "SELECT COUNT(*) FROM plan_validations WHERE project_id=? AND created_at>=? AND created_at<?"
            ),
            "successful_plan_validations": (
                "SELECT COUNT(*) FROM plan_validations WHERE project_id=? AND succeeded=1 AND created_at>=? AND created_at<?"
            ),
        }
        planning_structure = {
            name: connection.execute(query, (project[0], since, until)).fetchone()[0]
            for name, query in planning_queries.items()
        }
        return {
            "available": True,
            "project_id": project[0],
            "event_counts": event_counts,
            "planning_structure": planning_structure,
        }
    finally:
        connection.close()


def main() -> None:
    args = arguments()
    repo = args.repo.resolve()
    since = normalized_timestamp(args.since)
    until = normalized_timestamp(args.until)
    cohorts: dict[str, dict] = defaultdict(
        lambda: {
            "files": 0,
            "token_events": 0,
            "compactions": 0,
            "tokens": Counter(),
            "taskledger_commands": Counter(),
            "thread_sources": Counter(),
            "agent_roles": Counter(),
        }
    )
    files_scanned = files_matched = 0
    inherited_history_files = 0
    inherited_token_events_excluded = 0
    inherited_compactions_excluded = 0
    cli_versions = Counter()

    for path in args.sessions.rglob("*.jsonl"):
        files_scanned += 1
        metadata = session_metadata(path)
        if not metadata or not belongs_to_repo(metadata.get("cwd"), repo):
            continue
        if args.session_id and metadata.get("session_id") != args.session_id:
            continue
        files_matched += 1
        inherited_cutoff = inherited_history_cutoff(path, metadata)
        if inherited_cutoff:
            inherited_history_files += 1
        cli_versions[str(metadata.get("cli_version", "unknown"))] += 1
        model = "unknown"
        effort = "unknown"
        seen_cohorts: set[str] = set()
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                timestamp = item.get("timestamp")
                if not timestamp:
                    continue
                timestamp = normalized_timestamp(timestamp)
                if not since <= timestamp < until:
                    continue
                payload = item.get("payload", {})
                if item.get("type") == "turn_context":
                    model = payload.get("model", model)
                    collaboration = payload.get("collaboration_mode", {}).get("settings", {})
                    effort = payload.get("effort", collaboration.get("reasoning_effort", effort))
                    continue
                if item.get("type") == "world_state":
                    state = payload.get("state", {})
                    model = state.get("model", model)
                    effort = state.get("model_reasoning_effort", state.get("reasoning_effort", effort))
                    continue
                if payload.get("type") == "thread_settings_applied":
                    settings = payload.get("thread_settings", {})
                    model = settings.get("model", model)
                    effort = settings.get("model_reasoning_effort", settings.get("reasoning_effort", effort))
                    continue
                if inherited_cutoff and timestamp <= inherited_cutoff:
                    if payload.get("type") == "token_count":
                        inherited_token_events_excluded += 1
                    elif payload.get("type") == "context_compacted":
                        inherited_compactions_excluded += 1
                    continue
                cohort = f"{model}|{effort}"
                bucket = cohorts[cohort]
                seen_cohorts.add(cohort)
                if payload.get("type") == "token_count":
                    usage = payload.get("info", {}).get("last_token_usage", {})
                    if usage:
                        bucket["token_events"] += 1
                        for field in TOKEN_FIELDS:
                            bucket["tokens"][field] += int(usage.get(field, 0) or 0)
                if payload.get("type") == "context_compacted":
                    bucket["compactions"] += 1
                if item.get("type") == "response_item" and payload.get("type") == "custom_tool_call":
                    tool_input = payload.get("input", "")
                    for resource, action in COMMAND.findall(tool_input):
                        bucket["taskledger_commands"][f"{resource} {action}"] += 1
        for cohort in seen_cohorts:
            bucket = cohorts[cohort]
            bucket["files"] += 1
            bucket["thread_sources"][str(metadata.get("thread_source", "unknown"))] += 1
            bucket["agent_roles"][str(metadata.get("agent_role", "none"))] += 1

    serializable = {}
    for cohort, bucket in sorted(cohorts.items()):
        if not bucket["token_events"] and not bucket["compactions"] and not bucket["taskledger_commands"]:
            continue
        serializable[cohort] = {
            "files": bucket["files"],
            "token_events": bucket["token_events"],
            "compactions": bucket["compactions"],
            "tokens": dict(bucket["tokens"]),
            "taskledger_commands": dict(bucket["taskledger_commands"].most_common()),
            "thread_sources": dict(bucket["thread_sources"]),
            "agent_roles": dict(bucket["agent_roles"]),
        }

    result = {
        "schema_version": 1,
        "label": args.label,
        "taskledger_version": args.taskledger_version,
        "repository": str(repo),
        "window": {"since": since, "until": until},
        "source": {
            "sessions": str(args.sessions),
            "session_id": args.session_id,
            "files_scanned": files_scanned,
            "files_matched": files_matched,
            "codex_cli_versions": dict(cli_versions),
            "inherited_history": {
                "forked_files": inherited_history_files,
                "token_events_excluded": inherited_token_events_excluded,
                "compactions_excluded": inherited_compactions_excluded,
            },
        },
        "cohorts": serializable,
        "durable_progress": ledger_events(args.ledger_db, repo, since, until),
        "interpretation": {
            "primary_comparison": "Compare identical model and reasoning-effort cohorts per durable progress event.",
            "do_not_do": "Do not convert lower-tier token counts into hypothetical higher-tier behavior.",
            "cross_model_fallback": "Report each model separately; optionally weight within-model post/pre ratios by the baseline model mix.",
            "credits": "Use observed provider credits/rate versions only; this collector does not invent prices.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
