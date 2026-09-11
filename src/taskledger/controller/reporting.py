from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Awaitable, Callable

from .model import UsagePrecision


ProviderLoader = Callable[[str], Awaitable[dict[str, Any]]]


def _ms(start: str | None, finish: str | None) -> int:
    if not start or not finish:
        return 0
    return max(0, int((datetime.fromisoformat(finish.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds() * 1000))


def _interval_union(intervals: list[tuple[str, str]]) -> int:
    values = sorted((datetime.fromisoformat(a.replace("Z", "+00:00")), datetime.fromisoformat(b.replace("Z", "+00:00"))) for a, b in intervals if a and b)
    if not values:
        return 0
    total = 0.0
    start, end = values[0]
    for next_start, next_end in values[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += (end - start).total_seconds()
            start, end = next_start, next_end
    total += (end - start).total_seconds()
    return max(0, int(total * 1000))


def _usage_bucket() -> dict[str, int]:
    return {"turn_count": 0, "input_tokens": 0, "cached_input_tokens": 0, "non_cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "admission_tokens": 0, "summed_turn_duration_ms": 0}


ROLE_NAMES = (
    "Task Creator",
    "Routine Worker",
    "Complex Worker",
    "Submission Reviewer",
    "Checkpoint Reviewer",
    "Final Reviewer",
)


def _add(bucket: dict[str, int], row) -> None:
    bucket["turn_count"] += 1
    for name in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_tokens"):
        bucket[name] += int(row[name] or 0)
    bucket["non_cached_input_tokens"] += int(row["input_tokens"] or 0) - int(row["cached_input_tokens"] or 0)
    bucket["admission_tokens"] += int(row["input_tokens"] or 0) + int(row["output_tokens"] or 0)
    bucket["summed_turn_duration_ms"] += _ms(row["started_at"], row["finished_at"])


def _role_name(row) -> str:
    if row["role"] == "TASK_CREATOR":
        return "Task Creator"
    if row["role"] == "WORKER":
        return "Routine Worker" if row["profile"] == "routine" else "Complex Worker"
    if row["role"] == "REQUIREMENT_REVIEWER":
        return "Final Reviewer"
    if row["prompt_kind"] == "checkpoint-reviewer":
        return "Checkpoint Reviewer"
    return "Submission Reviewer"


async def controller_report(con, run_id: str, *, include_provider_detail: bool, provider_loader: ProviderLoader | None = None) -> dict[str, Any]:
    run = con.execute("SELECT * FROM controller_runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        raise ValueError("controller run was not found")
    config = json.loads(run["config_json"])
    manifest = config.get("manifest", {})
    turns = con.execute(
        "SELECT t.*,s.role,s.profile,s.subject_id,s.external_thread_id,s.runtime_identity_json FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=? ORDER BY t.started_at,t.id",
        (run_id,),
    ).fetchall()
    terminal = [row for row in turns if row["state"] in {"COMPLETED", "FAILED", "UNCERTAIN"}]
    completed = [row for row in terminal if row["state"] == "COMPLETED"]
    quality_counts = {precision.value.lower(): 0 for precision in UsagePrecision}
    for row in terminal:
        key = str(row["usage_precision"] or UsagePrecision.LEGACY_LAST_USAGE.value).lower()
        quality_counts[key] = quality_counts.get(key, 0) + 1

    totals = _usage_bucket()
    by_role: dict[str, dict[str, int]] = {name: _usage_bucket() for name in ROLE_NAMES}
    direct = _usage_bucket()
    shared = _usage_bucket()
    worker_intervals: list[tuple[str, str]] = []
    reviewer_intervals: list[tuple[str, str]] = []
    all_intervals: list[tuple[str, str]] = []
    by_model: dict[str, dict[str, int]] = {}
    by_reason: dict[str, dict[str, int]] = {}
    by_outcome: dict[str, dict[str, int]] = {}
    by_accounting: dict[str, dict[str, int]] = {}
    data_quality_errors: list[dict[str, str]] = []
    for row in terminal:
        if int(row["cached_input_tokens"] or 0) > int(row["input_tokens"] or 0):
            data_quality_errors.append({"turn_id": row["id"], "error": "cached input exceeds inclusive input"})
        name = _role_name(row)
        bucket = by_role[name]
        _add(bucket, row)
        _add(totals, row)
        _add(shared if name in {"Task Creator", "Final Reviewer"} else direct, row)
        identity = json.loads(row["runtime_identity_json"] or "{}")
        model_key = "|".join((str(identity.get("model") or "UNKNOWN"), str(identity.get("effort") or "UNKNOWN"), str(identity.get("protocol_identity") or "UNKNOWN")))
        reason_key = str(row["dispatch_reason"] or "UNKNOWN")
        outcome_key = str(row["state"] or "UNKNOWN")
        precision = str(row["usage_precision"] or UsagePrecision.LEGACY_LAST_USAGE.value)
        if row["usage_missing"]:
            accounting_key = "PARTIAL" if precision == UsagePrecision.PARTIAL_OBSERVATION.value else "MISSING"
        elif precision == UsagePrecision.SYNTHETIC_OR_ESTIMATED.value:
            accounting_key = "ESTIMATE"
        elif precision == UsagePrecision.LEGACY_LAST_USAGE.value:
            accounting_key = "LEGACY_METHOD"
        else:
            accounting_key = "COMPLETE"
        for collection, key in ((by_model, model_key), (by_reason, reason_key), (by_outcome, outcome_key), (by_accounting, accounting_key)):
            _add(collection.setdefault(key, _usage_bucket()), row)
        if row["finished_at"]:
            interval = (row["started_at"], row["finished_at"])
            all_intervals.append(interval)
            if row["role"] == "WORKER":
                worker_intervals.append(interval)
            elif row["role"] in {"REVIEWER", "REQUIREMENT_REVIEWER"}:
                reviewer_intervals.append(interval)

    events = [dict(row) for row in con.execute("SELECT * FROM controller_events WHERE run_id=? ORDER BY sequence", (run_id,))]
    waits: dict[str, int] = defaultdict(int)
    by_scope: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_scope[(event["scope_type"], event["scope_id"])].append(event)
    cutoff = run["finished_at"] or max(
        [run["started_at"]]
        + [row["finished_at"] or row["started_at"] for row in turns]
        + [event["occurred_at"] for event in events]
    )
    for scoped in by_scope.values():
        for index, event in enumerate(scoped):
            if event["reason_code"] and str(event["reason_code"]).startswith("WAITING_"):
                end = scoped[index + 1]["occurred_at"] if index + 1 < len(scoped) else cutoff
                waits[event["reason_code"]] += _ms(event["occurred_at"], end)
    run_events = by_scope.get(("RUN", run_id), [])
    for index, event in enumerate(run_events):
        if event["event_type"] == "RUN_PAUSED":
            end = run_events[index + 1]["occurred_at"] if index + 1 < len(run_events) else cutoff
            waits["HUMAN_OR_BLOCKER_PAUSE"] += _ms(event["occurred_at"], end)

    assignment_ids = sorted({
        row[0] for row in con.execute(
            "SELECT subject_id FROM controller_sessions WHERE run_id=? AND role='WORKER'",
            (run_id,),
        )
    })
    assignments = (
        con.execute(
            "SELECT a.*,t.state task_state FROM assignments a JOIN tasks t ON t.id=a.task_id "
            f"WHERE a.id IN ({','.join('?' for _ in assignment_ids)}) ORDER BY a.created_at,a.id",
            assignment_ids,
        ).fetchall()
        if assignment_ids else []
    )
    worker_turns = [row for row in completed if row["role"] == "WORKER"]
    one_turn = 0
    assignment_turn_counts: dict[str, int] = defaultdict(int)
    for row in worker_turns:
        assignment_turn_counts[row["subject_id"]] += 1
    for assignment in assignments:
        if assignment_turn_counts[assignment["id"]] == 1 and con.execute("SELECT 1 FROM submissions WHERE assignment_id=?", (assignment["id"],)).fetchone():
            one_turn += 1
    latest_worker_sequence: dict[str, int] = defaultdict(int)
    for row in worker_turns:
        latest_worker_sequence[row["session_id"]] = max(latest_worker_sequence[row["session_id"]], int(row["sequence"]))
    progressing = sum(
        bool(row["progressed"]) and int(row["sequence"]) < latest_worker_sequence[row["session_id"]]
        for row in worker_turns
    )
    continuations = sum(row["dispatch_reason"] in {"ACTIVE_CONTINUATION", "CORRECTION", "CHECKPOINT_CONTINUATION", "QUESTION_ANSWERED"} for row in worker_turns)
    no_progress_turns = sum(row["progressed"] == 0 for row in worker_turns if row["progressed"] is not None)

    routing: dict[str, Any] = {}
    for profile in ("routine", "complex"):
        rows = [a for a in assignments if a["worker_profile"] == profile]
        ids = [a["id"] for a in rows]
        submissions = (
            con.execute(
                "SELECT s.id,s.assignment_id,s.sequence,sv.outcome FROM submissions s "
                "LEFT JOIN submission_verifications sv ON sv.submission_id=s.id "
                f"WHERE s.assignment_id IN ({','.join('?' for _ in ids)}) "
                "ORDER BY s.assignment_id,s.sequence,sv.created_at",
                ids,
            ).fetchall()
            if ids else []
        )
        first = [s for s in submissions if s["sequence"] == 1 and s["outcome"]]
        rejected = [s for s in submissions if s["outcome"] == "REJECTED"]
        profile_turns = [row for row in worker_turns if row["profile"] == profile]
        routing[profile] = {
            "tasks": len({a["task_id"] for a in rows}),
            "first_submission_acceptance_rate": (sum(s["outcome"] == "ACCEPTED" for s in first) / len(first)) if first else None,
            "average_worker_turns": (len(profile_turns) / len(ids)) if ids else None,
            "average_worker_usage_tokens": (sum(int(t["input_tokens"] or 0) + int(t["output_tokens"] or 0) for t in profile_turns) / len(ids)) if ids else None,
            "review_rejection_rate": (len(rejected) / len([s for s in submissions if s["outcome"]])) if any(s["outcome"] for s in submissions) else None,
            "correction_rounds": len(rejected),
        }
    routine_task_ids = {a["task_id"] for a in assignments if a["worker_profile"] == "routine"}
    complex_task_ids = {a["task_id"] for a in assignments if a["worker_profile"] == "complex"}
    routing["routine"]["routine_to_complex_escalation_rate"] = (len(routine_task_ids & complex_task_ids) / len(routine_task_ids)) if routine_task_ids else None

    verification_rows = (
        con.execute(
            "SELECT sv.*,s.sequence,a.task_id FROM submission_verifications sv "
            "JOIN submissions s ON s.id=sv.submission_id JOIN assignments a ON a.id=s.assignment_id "
            f"WHERE a.id IN ({','.join('?' for _ in assignment_ids)}) AND sv.created_at>=? "
            "AND (? IS NULL OR sv.created_at<=?) ORDER BY sv.created_at,sv.id",
            (*assignment_ids, run["started_at"], run["finished_at"], run["finished_at"]),
        ).fetchall()
        if assignment_ids else []
    )
    checkpoint_rows = (
        con.execute(
            "SELECT c.* FROM assignment_checkpoints c "
            f"WHERE c.assignment_id IN ({','.join('?' for _ in assignment_ids)}) AND c.resolved_at IS NOT NULL "
            "AND c.resolved_at>=? AND (? IS NULL OR c.resolved_at<=?) ORDER BY c.resolved_at,c.id",
            (*assignment_ids, run["started_at"], run["finished_at"], run["finished_at"]),
        ).fetchall()
        if assignment_ids else []
    )
    final_rows = con.execute("SELECT * FROM controller_final_reviews WHERE run_id=? ORDER BY created_at,id", (run_id,)).fetchall()
    final_findings = [item for row in final_rows if row["verdict_json"] for item in json.loads(row["verdict_json"]).get("findings", [])]
    accepted_tasks = {row["task_id"] for row in verification_rows if row["outcome"] == "ACCEPTED"}
    affected_final = {rid for item in final_findings if item.get("classification") == "IMPLEMENTATION_DEFECT" for rid in item.get("requirement_ids", [])}
    escaped = 0
    for task_id in accepted_tasks:
        links = {row[0] for row in con.execute("SELECT requirement_id FROM task_requirement_links l JOIN tasks t ON t.id=l.task_id AND t.current_revision=l.task_revision WHERE l.task_id=?", (task_id,))}
        escaped += bool(links & affected_final)

    receipt_rows = (
        con.execute(
            "SELECT * FROM execution_receipts "
            f"WHERE assignment_id IN ({','.join('?' for _ in assignment_ids)}) AND started_at>=? "
            "AND (? IS NULL OR started_at<=?) ORDER BY started_at,id",
            (*assignment_ids, run["started_at"], run["finished_at"], run["finished_at"]),
        ).fetchall()
        if assignment_ids else []
    )
    failed_receipts = [row for row in receipt_rows if row["status"] != "SUCCEEDED"]
    target_task_ids = sorted({row["task_id"] for row in assignments} | {
        row[0] for row in con.execute("SELECT task_id FROM controller_run_targets WHERE run_id=?", (run_id,))
    })
    integration_rows = (
        con.execute(
            "SELECT * FROM integration_attempts "
            f"WHERE task_id IN ({','.join('?' for _ in target_task_ids)}) AND started_at>=? "
            "AND (? IS NULL OR started_at<=?) ORDER BY started_at,id",
            (*target_task_ids, run["started_at"], run["finished_at"], run["finished_at"]),
        ).fetchall()
        if target_task_ids else []
    )
    blocker_rows = con.execute(
        "SELECT * FROM blockers WHERE project_id=? AND created_at>=? "
        "AND (? IS NULL OR created_at<=?) ORDER BY created_at,id",
        (run["project_id"], run["started_at"], run["finished_at"], run["finished_at"]),
    ).fetchall()

    expected_turn_count = sum(bool(row["external_turn_id"]) for row in turns)
    provider = {"status": "NOT_REQUESTED", "available_recorded_turns": 0, "available_turns": 0, "expected_recorded_turns": expected_turn_count, "expected_turns": expected_turn_count, "missing_recorded_turn_ids": [], "unexpected_in_window_turn_ids": [], "unrelated_later_turn_count": 0, "coverage_complete": False, "complete": False}
    activity = {"command_count": 0, "command_failure_count": 0, "command_duration_ms": 0, "file_change_count": 0, "context_compaction_count": 0, "dynamic_tool_call_count": 0, "mcp_call_count": 0, "collaboration_agent_call_count": 0, "subagent_activity_count": 0, "codex_turn_duration_ms": 0}
    if include_provider_detail:
        provider["status"] = "UNAVAILABLE"
        if provider_loader is not None:
            available_ids: set[str] = set()
            unexpected_in_window: set[str] = set()
            unrelated_later = 0
            unknown_item_types: set[str] = set()
            expected_ids = {row["external_turn_id"] for row in turns if row["external_turn_id"]}
            try:
                for thread_id in sorted({row["external_thread_id"] for row in turns}):
                    history = await asyncio.wait_for(provider_loader(thread_id), timeout=5.0)
                    for turn in history.get("turns", []):
                        turn_id = turn.get("id")
                        if turn_id:
                            available_ids.add(turn_id)
                        stamp = turn.get("completedAt") or turn.get("createdAt") or turn.get("startedAt")
                        if turn_id not in expected_ids and stamp:
                            if run["started_at"] <= stamp <= cutoff:
                                unexpected_in_window.add(turn_id)
                            elif stamp > cutoff:
                                unrelated_later += 1
                        if turn_id in expected_ids:
                            activity["codex_turn_duration_ms"] += int(turn.get("durationMs") or 0)
                    for entry in history.get("items", []):
                        if entry.get("turnId") not in {row["external_turn_id"] for row in turns}:
                            continue
                        item = entry.get("item", {})
                        kind = item.get("type")
                        if kind == "commandExecution":
                            activity["command_count"] += 1
                            activity["command_failure_count"] += item.get("status") in {"failed", "declined"} or (isinstance(item.get("exitCode"), int) and item["exitCode"] != 0)
                            activity["command_duration_ms"] += int(item.get("durationMs") or 0)
                        elif kind == "fileChange": activity["file_change_count"] += 1
                        elif kind == "contextCompaction": activity["context_compaction_count"] += 1
                        elif kind == "dynamicToolCall": activity["dynamic_tool_call_count"] += 1
                        elif kind == "mcpToolCall": activity["mcp_call_count"] += 1
                        elif kind == "collabAgentToolCall": activity["collaboration_agent_call_count"] += 1
                        elif kind == "subAgentActivity": activity["subagent_activity_count"] += 1
                        elif kind not in {None, "agentMessage", "agent_message", "reasoning", "plan", "userMessage", "enteredReviewMode", "exitedReviewMode", "webSearch", "imageView"}:
                            unknown_item_types.add(str(kind))
                coverage_complete = expected_ids <= available_ids and not unknown_item_types
                available_count = len(expected_ids & available_ids)
                provider.update({"status": "AVAILABLE", "available_recorded_turns": available_count, "available_turns": available_count, "missing_recorded_turn_ids": sorted(expected_ids - available_ids), "unexpected_in_window_turn_ids": sorted(unexpected_in_window), "unrelated_later_turn_count": unrelated_later, "unknown_item_types": sorted(unknown_item_types), "coverage_complete": coverage_complete, "complete": coverage_complete})
            except Exception:
                provider.update({"status": "UNAVAILABLE", "available_recorded_turns": 0, "coverage_complete": False})

    controller_bytes = {name: sum(int(row[name] or 0) for row in turns) for name in ("controller_payload_bytes", "static_assignment_bytes", "dynamic_state_bytes", "correction_bytes", "output_schema_bytes")}
    session_measurements = []
    seen_sessions: set[str] = set()
    for row in turns:
        if row["session_id"] in seen_sessions:
            continue
        seen_sessions.add(row["session_id"])
        identity = json.loads(row["runtime_identity_json"] or "{}")
        session_measurements.append({
            "session_id": row["session_id"], "role": row["role"], "profile": row["profile"],
            "model": identity.get("model", "UNKNOWN"), "effort": identity.get("effort", "UNKNOWN"),
            "protocol_identity": identity.get("protocol_identity", "UNKNOWN"),
            "capability_policy_hash": identity.get("capability_policy_hash"),
            "base_instruction_bytes": int(identity.get("base_instruction_bytes") or 0),
            "profile_instruction_bytes": int(identity.get("profile_instruction_bytes") or 0),
            "dynamic_tool_schema_bytes": int(identity.get("dynamic_tool_schema_bytes") or 0),
            "dynamic_tool_count": int(identity.get("dynamic_tool_count") or 0),
        })
    wall_ms = _ms(run["started_at"], cutoff)
    paused_ms = waits.get("HUMAN_OR_BLOCKER_PAUSE", 0)
    active_wall_ms = max(0, wall_ms - paused_ms)
    limits = config.get("limits", {})
    worker_union = _interval_union(worker_intervals)
    reviewer_union = _interval_union(reviewer_intervals)
    worker_slot_ms = sum(_ms(start, finish) for start, finish in worker_intervals)
    reviewer_slot_ms = sum(_ms(start, finish) for start, finish in reviewer_intervals)
    submission_review_turns = [row for row in completed if _role_name(row) == "Submission Reviewer"]
    checkpoint_review_turns = [row for row in completed if _role_name(row) == "Checkpoint Reviewer"]
    final_review_turns = [row for row in completed if _role_name(row) == "Final Reviewer"]

    def average_review(rows, field: str) -> float | None:
        if not rows:
            return None
        if field == "tokens":
            return sum(int(row["input_tokens"] or 0) + int(row["output_tokens"] or 0) for row in rows) / len(rows)
        return sum(_ms(row["started_at"], row["finished_at"]) for row in rows) / len(rows)

    report = {
        "identity": {"run_id": run_id, "project_id": run["project_id"], "mode": run["mode"], "state": run["state"], "manifest": manifest},
        "scope": manifest.get("scope", "POST_APPROVAL_EXECUTION"),
        "quality": {"usage_accounting": quality_counts, "accounting_complete": not any(row["usage_missing"] for row in terminal), "data_quality_errors": data_quality_errors, "provider_detail": provider},
        "economics": {"totals": totals, "by_model": dict(sorted(by_model.items())), "by_role": {key: by_role[key] for key in sorted(by_role)}, "by_dispatch_reason": dict(sorted(by_reason.items())), "by_outcome": dict(sorted(by_outcome.items())), "by_accounting_class": dict(sorted(by_accounting.items())), "direct_task": direct, "shared_project": shared, "total_tokens_per_integrated_task_ratio": None, "valuation": {"status": "UNAVAILABLE", "reason": "no immutable valuation snapshot supplied"}},
        "scheduler": {"report_cutoff": cutoff, "gross_elapsed_ms": wall_ms, "wall_time_ms": wall_ms, "recorded_paused_ms": paused_ms, "non_paused_wall_ms": active_wall_ms, "summed_attempt_duration_ms": totals["summed_turn_duration_ms"], "summed_model_turn_duration_ms": totals["summed_turn_duration_ms"], "model_active_interval_union_ms": _interval_union(all_intervals), "worker_active_interval_union_ms": worker_union, "reviewer_active_interval_union_ms": reviewer_union, "waits_entity_ms": dict(sorted(waits.items())), "waits_ms": dict(sorted(waits.items())), "timing_provenance": "CONTROLLER_DISPATCH_TO_TERMINAL", "concurrency": {"max_workers": limits.get("max_workers", limits.get("supervisor", {}).get("max_workers")), "max_reviewers": limits.get("max_reviewers"), "max_inflight_targets": limits.get("max_inflight_targets"), "average_concurrent_workers": (worker_slot_ms / active_wall_ms) if active_wall_ms else None, "average_concurrent_reviewers": (reviewer_slot_ms / active_wall_ms) if active_wall_ms else None, "worker_slot_utilization": (worker_slot_ms / (active_wall_ms * limits.get("max_workers", 1))) if active_wall_ms and limits.get("max_workers") else None, "reviewer_slot_utilization": (reviewer_slot_ms / (active_wall_ms * limits.get("max_reviewers", 1))) if active_wall_ms and limits.get("max_reviewers") else None}},
        "workers": {"routine": routing["routine"], "complex": routing["complex"], "continuations": {"worker_assignments": len(assignments), "assignments_submitted_in_one_turn": one_turn, "progressing_early_stops": progressing, "automatic_continuation_turns": continuations, "by_dispatch_reason": {reason: sum(row["dispatch_reason"] == reason for row in worker_turns) for reason in ("ACTIVE_CONTINUATION", "CORRECTION", "CHECKPOINT_CONTINUATION", "QUESTION_ANSWERED", "STRUCTURED_OUTPUT_RETRY")}, "no_progress_turns": no_progress_turns, "stalls": sum(event["event_type"] == "RUN_PAUSED" and event["reason_code"] == "STALLED" for event in events), "human_continuation_interventions": None}},
        "reviews": {"submissions": {"total": len(verification_rows), "first_pass_accepted": sum(row["sequence"] == 1 and row["outcome"] == "ACCEPTED" for row in verification_rows), "first_pass_rejected": sum(row["sequence"] == 1 and row["outcome"] == "REJECTED" for row in verification_rows), "blocked": sum(row["outcome"] == "BLOCKED" for row in verification_rows), "correction_rounds": sum(row["outcome"] == "REJECTED" for row in verification_rows), "average_tokens": average_review(submission_review_turns, "tokens"), "average_duration_ms": average_review(submission_review_turns, "duration"), "escaped_defect_tasks": escaped}, "checkpoints": {"total": len(checkpoint_rows), "rejected": sum(row["state"] == "REJECTED" for row in checkpoint_rows), "average_tokens": average_review(checkpoint_review_turns, "tokens"), "average_duration_ms": average_review(checkpoint_review_turns, "duration")}, "final": {"reviews": len(final_rows), "average_tokens": average_review(final_review_turns, "tokens"), "average_duration_ms": average_review(final_review_turns, "duration"), "defects": sum(item.get("classification") == "IMPLEMENTATION_DEFECT" for item in final_findings), "product_ambiguities": sum(item.get("classification") == "PRODUCT_AMBIGUITY" for item in final_findings), "correction_tasks_created": sum(1 for row in con.execute("SELECT payload_json FROM audit_events WHERE project_id=? AND event_type='TASK_CREATED'", (run["project_id"],)) if any(json.loads(row[0]).get("plan_ref", "").startswith(f"controller-correction-{review['id']}-") for review in final_rows))}},
        "context_efficiency": {"controller_bytes": controller_bytes, "controller_byte_semantics": "correction_bytes may overlap dynamic_state_bytes; components are not summed into a payload total", "session_measurements": session_measurements, "token_usage": {key: totals[key] for key in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_tokens")}, "underlying_exact_response_count": sum(int(row["exact_response_count"] or 0) for row in terminal), "compactions": activity["context_compaction_count"], "provider_activity": activity},
        "reliability": {
            "runtime_failures": sum(row["state"] == "FAILED" for row in turns),
            "uncertain_outcomes": sum(row["state"] == "UNCERTAIN" for row in turns),
            "restarts": sum(event["event_type"] == "RUN_RESUMED" for event in events),
            "pauses": sum(event["event_type"] == "RUN_PAUSED" for event in events),
            "recoveries": sum(event["event_type"] == "RUN_RESUMED" for event in events),
            "required_checks": {
                "total": len(receipt_rows), "failed": len(failed_receipts),
                "failure_rate": (len(failed_receipts) / len(receipt_rows)) if receipt_rows else None,
                "summed_duration_ms": sum(_ms(row["started_at"], row["finished_at"]) for row in receipt_rows),
            },
            "blockers": {
                "total": len(blocker_rows), "unresolved": sum(row["state"] == "OPEN" for row in blocker_rows),
                "summed_duration_ms": sum(_ms(row["created_at"], row["resolved_at"] or run["finished_at"]) for row in blocker_rows),
            },
            "integration": {
                "attempts": len(integration_rows),
                "failed": sum(row["state"] == "FAILED" for row in integration_rows),
                "uncertain": sum(row["state"] == "UNCERTAIN" for row in integration_rows),
            },
        },
        "interventions": {"recorded_questions": con.execute("SELECT COUNT(*) FROM worker_questions WHERE assignment_id IN (SELECT subject_id FROM controller_sessions WHERE run_id=? AND role='WORKER')", (run_id,)).fetchone()[0], "recorded_pauses": sum(event["event_type"] == "RUN_PAUSED" for event in events), "explicit_resumes": sum(event["event_type"] == "RUN_RESUMED" and event["reason_code"] == "EXPLICIT_RESUME" for event in events), "budget_grants": con.execute("SELECT COUNT(*) FROM controller_budget_grants WHERE run_id=?", (run_id,)).fetchone()[0], "unobserved_manual_activity": "UNKNOWN"},
        "architecture_invariants": {"capabilities_enforced": "ENFORCED_BY_CONTROLLER_POLICY" if session_measurements and all(item["capability_policy_hash"] for item in session_measurements) else "UNKNOWN", "provider_evidence_coverage": provider["status"], "nested_agent_calls": activity["collaboration_agent_call_count"] if provider["coverage_complete"] else None, "subagent_activity": activity["subagent_activity_count"] if provider["coverage_complete"] else None, "nested_agent_calls_observed": activity["collaboration_agent_call_count"] if provider["coverage_complete"] else None, "subagent_activity_observed": activity["subagent_activity_count"] if provider["coverage_complete"] else None, "missing_usage": sum(bool(row["usage_missing"]) for row in terminal), "missing_usage_allocations": sum(bool(row["usage_missing"]) for row in terminal), "recorded_turns_missing_from_provider": len(provider["missing_recorded_turn_ids"]), "unexpected_provider_turns_in_window": len(provider["unexpected_in_window_turn_ids"]), "untracked_turns": len(provider["missing_recorded_turn_ids"]) if include_provider_detail else None},
    }
    integrated = con.execute("SELECT COUNT(*) FROM controller_run_targets WHERE run_id=? AND state='INTEGRATED'", (run_id,)).fetchone()[0]
    if integrated:
        report["economics"]["total_tokens_per_integrated_task_ratio"] = (totals["input_tokens"] + totals["output_tokens"]) / integrated
    return report
