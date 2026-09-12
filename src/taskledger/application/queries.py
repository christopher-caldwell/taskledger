from __future__ import annotations

import json
from typing import Any

from taskledger.core import LedgerError, now
from taskledger.controller.journal import Journal

from .contracts import ConsoleSnapshot, HistoryCursor, HistoryPage, Record, SnapshotVersion


def _value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((str(k), _value(v)) for k, v in sorted(value.items()))
    if isinstance(value, list):
        return tuple(_value(v) for v in value)
    return value


def record(row: Any = None, **values: Any) -> Record:
    source = dict(row) if row is not None else values
    safe = {}
    for key, value in source.items():
        if key.endswith("_json"):
            try: value = json.loads(value)
            except (TypeError, json.JSONDecodeError): pass
        if "token" in key.lower() and key not in {"input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens", "total_tokens"}:
            continue
        safe[key] = _value(value)
    return Record(tuple((str(k), safe[k]) for k in sorted(safe)))


class ConsoleQueries:
    def __init__(self, service: Any, project: Any, epoch: str, owns_run: callable):
        self.service, self.project, self.epoch, self.owns_run = service, dict(project), epoch, owns_run
        self.revision = 0

    def snapshot(self, operation: Record | None = None) -> ConsoleSnapshot:
        con, pid = self.service.con, self.project["id"]
        if con.in_transaction:
            raise RuntimeError("cannot project an open write transaction")
        con.execute("BEGIN")
        try:
            run = con.execute("SELECT * FROM controller_runs WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (pid,)).fetchone()
            prep = con.execute("SELECT * FROM project_preparations WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (pid,)).fetchone()
            tasks = con.execute("SELECT t.id,t.current_revision,t.state,t.updated_at,r.objective,r.implementation_scope FROM tasks t JOIN task_revisions r ON r.task_id=t.id AND r.revision=t.current_revision WHERE t.project_id=? ORDER BY t.created_at,t.id LIMIT 1001", (pid,)).fetchall()
            sessions = con.execute("SELECT s.id,s.run_id,s.role,s.profile,s.subject_id,s.external_thread_id,s.state,s.created_at,s.closed_at,(SELECT state FROM controller_turns WHERE session_id=s.id ORDER BY sequence DESC LIMIT 1) latest_turn_state FROM controller_sessions s WHERE s.project_id=? ORDER BY s.created_at DESC LIMIT 200", (pid,)).fetchall()
            questions = con.execute("SELECT q.id,'QUESTION' kind,q.body description,q.state,q.assignment_id scope_id,q.asked_at created_at FROM worker_questions q JOIN assignments a ON a.id=q.assignment_id WHERE a.project_id=? AND q.state='OPEN'", (pid,)).fetchall()
            blockers = con.execute("SELECT id,'BLOCKER' kind,description,state,scope_id,created_at FROM blockers WHERE project_id=? AND state='OPEN'", (pid,)).fetchall()
            audit = con.execute("SELECT sequence,'AUDIT' source,event_type,entity_type,entity_id,created_at FROM audit_events WHERE project_id=? ORDER BY sequence DESC LIMIT 100", (pid,)).fetchall()
            controller = con.execute("SELECT e.sequence,'CONTROLLER' source,e.event_type,e.scope_type entity_type,e.scope_id entity_id,e.occurred_at created_at FROM controller_events e JOIN controller_runs r ON r.id=e.run_id WHERE r.project_id=? ORDER BY e.sequence DESC LIMIT 100", (pid,)).fetchall()
            progress = self.service.progress(self.project)
            phase = self.service.phase(self.project)
            usage = Journal(con, self.service.home).usage_report(run_id=run["id"]) if run else {}
        finally:
            con.rollback()
        self.revision += 1
        run_values = dict(run) if run else None
        if run_values is not None: run_values["owned_by_host"] = self.owns_run(run_values["id"])
        events = sorted([record(x) for x in (*audit, *controller)], key=lambda x: (x.get("created_at", ""), x.get("source", ""), x.get("sequence", 0)), reverse=True)[:200]
        return ConsoleSnapshot(1, SnapshotVersion(self.epoch, self.revision), now(), record(**self.project, progress=progress, effective_phase=phase), record(prep) if prep else None, record(**run_values) if run_values else None, tuple(record(x) for x in tasks), tuple(record(x) for x in sessions), record(**usage), tuple(record(x) for x in (*questions, *blockers)), tuple(events), operation)

    def task_detail(self, task_id: str) -> Record:
        row = self.service.con.execute("SELECT t.*,r.objective,r.implementation_scope FROM tasks t JOIN task_revisions r ON r.task_id=t.id AND r.revision=t.current_revision WHERE t.id=? AND t.project_id=?", (task_id, self.project["id"])).fetchone()
        if not row: raise LedgerError("INVALID_REQUEST", "Task was not found.")
        criteria = [x[0] for x in self.service.con.execute("SELECT criterion_text FROM task_acceptance_criteria WHERE task_id=? AND task_revision=? ORDER BY position", (task_id, row["current_revision"]))]
        return record(**dict(row), acceptance_criteria=criteria)

    def preparation_detail(self, preparation_id: str) -> Record:
        row = self.service.con.execute("SELECT * FROM project_preparations WHERE id=? AND project_id=?", (preparation_id,self.project["id"])).fetchone()
        if not row: raise LedgerError("INVALID_REQUEST", "Preparation was not found.")
        return record(row)

    def history_page(self, cursor: HistoryCursor, limit: int = 100) -> HistoryPage:
        limit = max(1, min(limit, 500)); pid = self.project["id"]
        a = self.service.con.execute("SELECT sequence,'AUDIT' source,event_type,entity_type,entity_id,created_at FROM audit_events WHERE project_id=? AND sequence>? ORDER BY sequence LIMIT ?", (pid,cursor.audit_sequence,limit)).fetchall()
        c = self.service.con.execute("SELECT e.sequence,'CONTROLLER' source,e.event_type,e.scope_type entity_type,e.scope_id entity_id,e.occurred_at created_at FROM controller_events e JOIN controller_runs r ON r.id=e.run_id WHERE r.project_id=? AND e.sequence>? ORDER BY e.sequence LIMIT ?", (pid,cursor.controller_sequence,limit)).fetchall()
        rows = sorted((*a,*c), key=lambda x:(x["created_at"],x["source"],x["sequence"]))[:limit]
        ac = max((x["sequence"] for x in rows if x["source"]=="AUDIT"), default=cursor.audit_sequence)
        cc = max((x["sequence"] for x in rows if x["source"]=="CONTROLLER"), default=cursor.controller_sequence)
        return HistoryPage(tuple(record(x) for x in rows), HistoryCursor(ac,cc))
