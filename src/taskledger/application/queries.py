from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any

from taskledger.core import LedgerError, now
from taskledger.controller.journal import Journal

from .contracts import ConsoleSnapshot, HistoryCursor, HistoryPage, Record, SnapshotVersion, TaskPage


_PUBLIC_TOKEN_FIELDS = {
    "input_tokens", "output_tokens", "cached_input_tokens", "non_cached_input_tokens",
    "cache_write_input_tokens", "reasoning_tokens", "total_tokens", "admission_tokens",
    "known_tokens", "known_total_tokens", "known_token_subtotal",
    "aggregate_preparation_known_tokens", "direct_preparation_known_tokens",
    "preparation_known_tokens", "post_approval_execution_known_tokens",
}


def _forbidden_key(key: str) -> bool:
    lowered = key.lower()
    return ("token" in lowered and lowered not in _PUBLIC_TOKEN_FIELDS) or any(
        marker in lowered for marker in ("password", "credential", "authorization", "api_key", "secret")
    )


def _value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(
            (str(k), _value(v)) for k, v in sorted(value.items(), key=lambda item: str(item[0]))
            if not _forbidden_key(str(k))
        )
    if isinstance(value, (list, tuple)):
        return tuple(_value(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_value(v) for v in value), key=repr))
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return str(value)


def record(row: Any = None, **values: Any) -> Record:
    source = dict(row) if row is not None else values
    safe = {}
    for key, value in source.items():
        if key.endswith("_json"):
            try: value = json.loads(value)
            except (TypeError, json.JSONDecodeError): pass
        if _forbidden_key(key):
            continue
        safe[key] = _value(value)
    return Record(tuple((str(k), safe[k]) for k in sorted(safe)))


@contextmanager
def read_transaction(con):
    if con.in_transaction:
        raise RuntimeError("cannot query from an open write transaction")
    con.execute("BEGIN")
    try:
        yield
    finally:
        con.rollback()


class ConsoleQueries:
    def __init__(self, service: Any, project: Any, epoch: str, owns_run: callable, pause_requested: callable | None = None):
        self.service, self.project, self.epoch, self.owns_run = service, dict(project), epoch, owns_run
        self.pause_requested = pause_requested or (lambda _run_id: False)
        self.revision = 0

    def snapshot(self, operation: Record | None = None) -> ConsoleSnapshot:
        con, pid = self.service.con, self.project["id"]
        if con.in_transaction:
            raise RuntimeError("cannot project an open write transaction")
        with read_transaction(con):
            current_project = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            run = con.execute("SELECT id,project_id,mode,state,pause_reason,pause_detail,created_at,started_at,finished_at,config_json "
                "FROM controller_runs WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (pid,)).fetchone()
            prep = con.execute("SELECT id,project_id,planning_run_id,starting_oid,canonical_branch,specification_id,"
                "specification_hash,proposal_hash,state,failure_reason,execution_run_id,created_at,updated_at,approved_at "
                "FROM project_preparations WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (pid,)).fetchone()
            task_total = con.execute("SELECT COUNT(*) FROM tasks WHERE project_id=?", (pid,)).fetchone()[0]
            tasks = con.execute("SELECT t.id,t.current_revision,t.state,t.updated_at,r.objective,r.implementation_scope,"
                "(SELECT rt.wave FROM controller_run_targets rt JOIN controller_runs cr ON cr.id=rt.run_id WHERE rt.task_id=t.id ORDER BY cr.created_at DESC LIMIT 1) wave,"
                "(SELECT rt.worker_profile FROM controller_run_targets rt JOIN controller_runs cr ON cr.id=rt.run_id WHERE rt.task_id=t.id ORDER BY cr.created_at DESC LIMIT 1) worker_profile,"
                "(SELECT rt.assignment_id FROM controller_run_targets rt JOIN controller_runs cr ON cr.id=rt.run_id WHERE rt.task_id=t.id ORDER BY cr.created_at DESC LIMIT 1) assignment_id,"
                "(SELECT COUNT(*) FROM task_dependencies d WHERE d.task_id=t.id AND d.task_revision=t.current_revision) dependency_count,"
                "(SELECT COUNT(*) FROM blockers b WHERE b.project_id=t.project_id AND b.state='OPEN' AND b.scope_id=t.id) blocker_count,"
                "EXISTS(SELECT 1 FROM integration_attempts i WHERE i.task_id=t.id AND i.state='SUCCEEDED') integrated "
                "FROM tasks t JOIN task_revisions r ON r.task_id=t.id AND r.revision=t.current_revision "
                "WHERE t.project_id=? ORDER BY t.created_at,t.id LIMIT 1000", (pid,)).fetchall()
            sessions = con.execute("SELECT s.id,s.run_id,s.role,s.profile,s.subject_id,s.external_thread_id,s.state,s.created_at,s.closed_at,"
                "json_extract(s.runtime_identity_json,'$.model') model,json_extract(s.runtime_identity_json,'$.effort') effort,"
                "(SELECT state FROM controller_turns WHERE session_id=s.id ORDER BY sequence DESC LIMIT 1) latest_turn_state,"
                "(SELECT COUNT(*) FROM controller_turns WHERE session_id=s.id) turn_count "
                "FROM controller_sessions s WHERE s.project_id=? ORDER BY s.created_at DESC LIMIT 200", (pid,)).fetchall()
            questions = con.execute("SELECT q.id,'QUESTION' kind,q.body description,q.state,q.assignment_id scope_id,q.asked_at created_at FROM worker_questions q JOIN assignments a ON a.id=q.assignment_id WHERE a.project_id=? AND q.state='OPEN'", (pid,)).fetchall()
            blockers = con.execute("SELECT id,'BLOCKER' kind,description,state,scope_id,created_at FROM blockers WHERE project_id=? AND state='OPEN'", (pid,)).fetchall()
            uncertainty = con.execute("SELECT t.id,'UNCERTAIN_TURN' kind,t.error description,t.state,s.subject_id scope_id,t.finished_at created_at "
                "FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.project_id=? AND t.state='UNCERTAIN'", (pid,)).fetchall()
            audit = con.execute("SELECT sequence,'AUDIT' source,event_type,entity_type,entity_id,created_at FROM audit_events WHERE project_id=? ORDER BY sequence DESC LIMIT 100", (pid,)).fetchall()
            controller = con.execute("SELECT e.sequence,'CONTROLLER' source,e.event_type,e.scope_type entity_type,e.scope_id entity_id,e.occurred_at created_at FROM controller_events e JOIN controller_runs r ON r.id=e.run_id WHERE r.project_id=? ORDER BY e.sequence DESC LIMIT 100", (pid,)).fetchall()
            progress = self.service.progress(self.project)
            phase = self.service.phase(current_project)
            usage = Journal(con, self.service.home).usage_report(run_id=run["id"]) if run else {}
            requirements = con.execute("SELECT COUNT(*) total,SUM(CASE WHEN lifecycle='ACTIVE' THEN 1 ELSE 0 END) active FROM requirements WHERE project_id=?", (pid,)).fetchone()
            verified = con.execute("SELECT COUNT(*) FROM requirement_verifications WHERE project_id=? AND state='CURRENT'", (pid,)).fetchone()[0]
            last_audit = con.execute("SELECT MAX(created_at) FROM audit_events WHERE project_id=?", (pid,)).fetchone()[0]
            last_controller = con.execute("SELECT MAX(e.occurred_at) FROM controller_events e JOIN controller_runs r ON r.id=e.run_id WHERE r.project_id=?", (pid,)).fetchone()[0]
            grants = con.execute("SELECT kind,SUM(amount) amount FROM controller_budget_grants WHERE run_id=? GROUP BY kind", (run["id"],)).fetchall() if run else []
        self.revision += 1
        run_values = dict(run) if run else None
        configured_limits = {}
        if run_values is not None:
            configured_limits = json.loads(run_values.pop("config_json")).get("limits", {})
            run_values["owned_by_host"] = self.owns_run(run_values["id"])
            run_values["pause_requested"] = self.pause_requested(run_values["id"])
        events = sorted([record(x) for x in (*audit, *controller)], key=lambda x: (x.get("created_at", ""), x.get("source", ""), x.get("sequence", 0)), reverse=True)[:200]
        project_values = dict(current_project)
        owned_running = bool(run_values and run_values["state"] == "RUNNING" and run_values["owned_by_host"])
        actions = [
            {"code": "PREPARE", "enabled": not bool(run_values and run_values["state"] == "RUNNING"),
             "reason": None if not (run_values and run_values["state"] == "RUNNING") else "A controller run is active."},
            {"code": "START_PREPARED", "enabled": bool(prep and prep["state"] == "AWAITING_APPROVAL"),
             "reason": None if prep and prep["state"] == "AWAITING_APPROVAL" else "No preparation is awaiting approval."},
            {"code": "RESUME", "enabled": bool(run_values and run_values["mode"] in {"PROJECT", "ASSIGNMENT"} and run_values["state"] in {"PAUSED", "FAILED"}),
             "reason": None if run_values and run_values["mode"] in {"PROJECT", "ASSIGNMENT"} and run_values["state"] in {"PAUSED", "FAILED"} else "No resumable execution run."},
            {"code": "PAUSE", "enabled": owned_running,
             "reason": None if owned_running else "No running lifecycle is owned by this host."},
        ]
        project_values.update(progress=progress, effective_phase=phase, requirement_count=requirements["total"],
            active_requirement_count=requirements["active"] or 0, verified_requirement_count=verified,
            task_count=task_total, tasks_truncated=task_total > len(tasks),
            last_durable_observation=max((value for value in (last_audit, last_controller) if value), default=None),
            cache_health="HEALTHY", available_actions=actions)
        usage_values = dict(usage)
        usage_values["grants"] = [dict(item) for item in grants]
        usage_values["configured_limits"] = configured_limits
        interventions = [record(x) for x in (*questions, *blockers, *uncertainty)]
        if prep and prep["state"] == "AWAITING_APPROVAL":
            interventions.append(record(id=prep["id"], kind="PREPARATION_APPROVAL", description="Exact proposal approval required",
                state=prep["state"], scope_id=prep["id"], created_at=prep["created_at"]))
        return ConsoleSnapshot(1, SnapshotVersion(self.epoch, self.revision), now(), record(**project_values), record(prep) if prep else None, record(**run_values) if run_values else None, tuple(record(x) for x in tasks), tuple(record(x) for x in sessions), record(**usage_values), tuple(interventions), tuple(events), operation)

    def task_detail(self, task_id: str) -> Record:
        con = self.service.con
        with read_transaction(con):
            row = con.execute("SELECT t.*,r.objective,r.implementation_scope FROM tasks t JOIN task_revisions r ON r.task_id=t.id AND r.revision=t.current_revision WHERE t.id=? AND t.project_id=?", (task_id, self.project["id"])).fetchone()
            if not row: raise LedgerError("INVALID_REQUEST", "Task was not found.")
            row, revision = dict(row), row["current_revision"]
            criteria = [x[0] for x in con.execute("SELECT criterion_text FROM task_acceptance_criteria WHERE task_id=? AND task_revision=? ORDER BY position", (task_id, revision))]
            checks = [x[0] for x in con.execute("SELECT command FROM task_required_checks WHERE task_id=? AND task_revision=? ORDER BY position", (task_id, revision))]
            dependencies = [x[0] for x in con.execute("SELECT depends_on_task_id FROM task_dependencies WHERE task_id=? AND task_revision=? ORDER BY depends_on_task_id", (task_id, revision))]
            requirements = [x[0] for x in con.execute("SELECT requirement_id FROM task_requirement_links WHERE task_id=? AND task_revision=? ORDER BY requirement_id", (task_id, revision))]
            assignments = [dict(x) for x in con.execute("SELECT id,state,attempt_number,worker_profile,execution_mode,created_at,activated_at,closed_at FROM assignments WHERE task_id=? ORDER BY attempt_number", (task_id,))]
            submissions = [dict(x) for x in con.execute("SELECT id,assignment_id,sequence,state,summary,head_commit_oid,evidence_json,submitted_at,resolved_at FROM submissions WHERE project_id=? AND assignment_id IN (SELECT id FROM assignments WHERE task_id=?) ORDER BY submitted_at,id", (self.project["id"], task_id))]
            verifications = [dict(x) for x in con.execute(
                "SELECT v.id,v.submission_id,v.outcome,v.criterion_results_json,v.behavior_matches_intent,"
                "v.required_evidence_present,v.blocking_issues_remaining,v.corrections,v.notes,v.blocker_id,v.created_at "
                "FROM submission_verifications v JOIN submissions s ON s.id=v.submission_id "
                "WHERE s.project_id=? AND s.assignment_id IN (SELECT id FROM assignments WHERE task_id=?) "
                "ORDER BY v.created_at,v.id", (self.project["id"], task_id))]
            checkpoints = [dict(x) for x in con.execute(
                "SELECT id,assignment_id,sequence,task_revision,label,criteria_json,state,summary,head_commit_oid,"
                "evidence_json,submitted_at,resolved_at,criterion_results_json,corrections,notes "
                "FROM assignment_checkpoints WHERE assignment_id IN (SELECT id FROM assignments WHERE task_id=?) "
                "ORDER BY assignment_id,sequence,id", (task_id,))]
            receipts = [dict(x) for x in con.execute("SELECT id,assignment_id,submission_id,execution_role,command,status,exit_code,output_artifact_id,started_at,finished_at FROM execution_receipts WHERE project_id=? AND assignment_id IN (SELECT id FROM assignments WHERE task_id=?) ORDER BY started_at,id", (self.project["id"], task_id))]
            evidence = [dict(x) for x in con.execute("SELECT id,assignment_id,provenance_kind,original_path,stored_path,sha256,size_bytes,source_revision,created_at FROM evidence_artifacts WHERE project_id=? AND assignment_id IN (SELECT id FROM assignments WHERE task_id=?) ORDER BY created_at,id", (self.project["id"], task_id))]
            integrations = [dict(x) for x in con.execute("SELECT id,submission_id,state,canonical_before_oid,accepted_head_oid,canonical_after_oid,started_at,finished_at FROM integration_attempts WHERE project_id=? AND task_id=? ORDER BY started_at,id", (self.project["id"], task_id))]
        return record(**row, acceptance_criteria=criteria, required_checks=checks, dependencies=dependencies,
            requirement_ids=requirements, assignments=assignments, submissions=submissions,
            submission_verifications=verifications, checkpoints=checkpoints,
            execution_receipts=receipts, evidence=evidence, integration_attempts=integrations)

    def preparation_detail(self, preparation_id: str) -> Record:
        con = self.service.con
        with read_transaction(con):
            row = con.execute("SELECT * FROM project_preparations WHERE id=? AND project_id=?", (preparation_id,self.project["id"])).fetchone()
            if not row: raise LedgerError("INVALID_REQUEST", "Preparation was not found.")
            row = dict(row)
            attempts = [dict(x) for x in con.execute(
                "SELECT a.id,a.run_group_id,a.sequence,a.planning_run_id,a.state,a.failure_reason,a.created_at,a.updated_at "
                "FROM preparation_attempts a WHERE a.preparation_id=? ORDER BY a.sequence,a.id", (preparation_id,))]
        return record(**row, attempts=attempts)

    def task_page(self, offset: int = 0, limit: int = 200) -> TaskPage:
        offset, limit = max(0, offset), max(1, min(limit, 500))
        with read_transaction(self.service.con):
            total = self.service.con.execute("SELECT COUNT(*) FROM tasks WHERE project_id=?", (self.project["id"],)).fetchone()[0]
            rows = self.service.con.execute(
                "SELECT t.id,t.current_revision,t.state,t.updated_at,r.objective,r.implementation_scope "
                "FROM tasks t JOIN task_revisions r ON r.task_id=t.id AND r.revision=t.current_revision "
                "WHERE t.project_id=? ORDER BY t.created_at,t.id LIMIT ? OFFSET ?", (self.project["id"], limit, offset)).fetchall()
        following = offset + len(rows)
        return TaskPage(tuple(record(x) for x in rows), following if following < total else None, total)

    def history_page(self, cursor: HistoryCursor, limit: int = 100) -> HistoryPage:
        limit = max(1, min(limit, 500)); pid = self.project["id"]
        with read_transaction(self.service.con):
            a = self.service.con.execute("SELECT sequence,'AUDIT' source,event_type,entity_type,entity_id,created_at FROM audit_events WHERE project_id=? AND sequence>? ORDER BY sequence LIMIT ?", (pid,cursor.audit_sequence,limit)).fetchall()
            c = self.service.con.execute("SELECT e.sequence,'CONTROLLER' source,e.event_type,e.scope_type entity_type,e.scope_id entity_id,e.occurred_at created_at FROM controller_events e JOIN controller_runs r ON r.id=e.run_id WHERE r.project_id=? AND e.sequence>? ORDER BY e.sequence LIMIT ?", (pid,cursor.controller_sequence,limit)).fetchall()
        rows = sorted((*a,*c), key=lambda x:(x["created_at"],x["source"],x["sequence"]))[:limit]
        ac = max((x["sequence"] for x in rows if x["source"]=="AUDIT"), default=cursor.audit_sequence)
        cc = max((x["sequence"] for x in rows if x["source"]=="CONTROLLER"), default=cursor.controller_sequence)
        return HistoryPage(tuple(record(x) for x in rows), HistoryCursor(ac,cc))
