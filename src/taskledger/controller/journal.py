from __future__ import annotations

import contextlib
import hashlib
import json
import os
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from taskledger.core import canonical, new_id, now
from taskledger.db import transaction

from .model import DispatchReason, PromptPacket, RuntimeTurnHandle, RuntimeTurnResult, Usage, UsagePrecision

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


@dataclass(frozen=True)
class AgentSession:
    id: str
    run_id: str
    project_id: str
    role: str
    profile: str
    subject_id: str
    thread_id: str
    config_hash: str
    runtime_identity: dict[str, Any]
    state: str


@dataclass(frozen=True)
class OpenTurn:
    id: str
    session_id: str
    thread_id: str
    state: str
    external_turn_id: str | None


@dataclass(frozen=True)
class TerminalTurn:
    id: str
    session_id: str
    state: str
    prompt_kind: str
    thread_id: str
    external_turn_id: str | None
    result: RuntimeTurnResult | None
    error: str | None
    progress_before: str | None
    progress_after: str | None
    progressed: bool | None


class Journal:
    """Controller state stored beside, but separate from, Taskledger domain rows."""

    def __init__(self, con, home: Path):
        self.con = con
        self.home = home

    @contextlib.contextmanager
    def project_lock(self, project_key: str) -> Iterator[None]:
        if fcntl is None:
            raise RuntimeError("controller locking requires fcntl")
        lock_dir = self.home / "controller-locks"
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        digest = hashlib.sha256(project_key.encode()).hexdigest()[:24]
        lock_path = lock_dir / f"{digest}.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("controller already running for this project") from exc
            os.ftruncate(fd, 0)
            os.write(fd, f"pid={os.getpid()}\nproject={project_key}\n".encode())
            os.fsync(fd)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def create_run(self, project_id: str, *, mode: str, config: dict[str, Any]) -> str:
        if mode not in {"ASSIGNMENT", "PROJECT"}:
            raise ValueError(mode)
        run_id = new_id()
        stamp = now()
        with transaction(self.con):
            active = self.con.execute(
                "SELECT id FROM controller_runs WHERE project_id=? AND state='RUNNING' LIMIT 1",
                (project_id,),
            ).fetchone()
            if active:
                raise RuntimeError(f"controller run {active['id']} is already running for this project")
            self.con.execute(
                "INSERT INTO controller_runs(id,project_id,mode,state,config_json,created_at,started_at) VALUES(?,?,?,?,?,?,?)",
                (run_id, project_id, mode, "RUNNING", canonical(config), stamp, stamp),
            )
            self._append_event_locked(run_id, "RUN", run_id, "RUN_STARTED", "INITIAL_START", None, {})
        return run_id

    def create_project_run(self, project_id: str, *, config: dict[str, Any], targets: list[dict[str, Any]]) -> str:
        run_id = new_id()
        stamp = now()
        with transaction(self.con):
            active = self.con.execute(
                "SELECT id FROM controller_runs WHERE project_id=? AND state='RUNNING' LIMIT 1", (project_id,)
            ).fetchone()
            if active:
                raise RuntimeError(f"controller run {active['id']} is already running for this project")
            self.con.execute(
                "INSERT INTO controller_runs(id,project_id,mode,state,config_json,created_at,started_at) VALUES(?,?,?,?,?,?,?)",
                (run_id, project_id, "PROJECT", "RUNNING", canonical(config), stamp, stamp),
            )
            self._append_event_locked(run_id, "RUN", run_id, "RUN_STARTED", "INITIAL_START", None, {})
            for target in targets:
                self.con.execute(
                    "INSERT INTO controller_run_targets(run_id,task_id,wave,worker_profile,parallel_safe,write_surfaces_json,assignment_id,state) VALUES(?,?,?,?,?,?,NULL,'QUEUED')",
                    (run_id, target["task_id"], target["wave"], target["worker_profile"], int(target["parallel_safe"]), canonical(target["write_surfaces"])),
                )
        return run_id

    def resume_run(self, run_id: str) -> dict[str, Any]:
        row = self.con.execute("SELECT * FROM controller_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise RuntimeError("controller run was not found")
        if row["state"] == "COMPLETED":
            raise RuntimeError("completed controller run cannot be resumed")
        with transaction(self.con):
            active = self.con.execute(
                "SELECT id FROM controller_runs WHERE project_id=? AND state='RUNNING' AND id<>? LIMIT 1",
                (row["project_id"], run_id),
            ).fetchone()
            if active:
                raise RuntimeError(f"controller run {active['id']} is already running for this project")
            self.con.execute(
                "UPDATE controller_runs SET state='RUNNING',pause_reason=NULL,pause_detail=NULL,finished_at=NULL WHERE id=?",
                (run_id,),
            )
            self._append_event_locked(run_id, "RUN", run_id, "RUN_RESUMED", "EXPLICIT_RESUME", None, {})
        result = dict(row)
        result["config"] = json.loads(result.pop("config_json"))
        return result

    def finish_run(self, run_id: str, state: str, *, reason: str | None = None, detail: str | None = None) -> None:
        if state not in {"COMPLETED", "PAUSED", "FAILED"}:
            raise ValueError(state)
        with transaction(self.con):
            self.con.execute(
                "UPDATE controller_runs SET state=?,pause_reason=?,pause_detail=?,finished_at=? WHERE id=?",
                (state, reason, detail, now(), run_id),
            )
            self._append_event_locked(
                run_id, "RUN", run_id, "RUN_" + state,
                reason, None, {"detail_hash": hashlib.sha256((detail or "").encode()).hexdigest() if detail else None},
            )

    def run(self, run_id: str) -> dict[str, Any] | None:
        row = self.con.execute("SELECT * FROM controller_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        value = dict(row)
        value["config"] = json.loads(value.pop("config_json"))
        return value

    def find_active_session(self, *, project_id: str, role: str, subject_id: str) -> AgentSession | None:
        row = self.con.execute(
            "SELECT * FROM controller_sessions WHERE project_id=? AND role=? AND subject_id=? AND state='ACTIVE'",
            (project_id, role, subject_id),
        ).fetchone()
        return self._session(row) if row else None

    def active_sessions(self, *, project_id: str, role: str | None = None) -> list[AgentSession]:
        sql = "SELECT * FROM controller_sessions WHERE project_id=? AND state='ACTIVE'"
        params: tuple[Any, ...] = (project_id,)
        if role is not None:
            sql += " AND role=?"
            params += (role,)
        rows = self.con.execute(sql + " ORDER BY created_at,id", params).fetchall()
        return [self._session(row) for row in rows]

    def create_session(
        self,
        *,
        run_id: str,
        project_id: str,
        role: str,
        profile: str,
        subject_id: str,
        external_thread_id: str,
        config_hash: str,
        runtime_identity: dict[str, Any] | None = None,
    ) -> AgentSession:
        session_id = new_id()
        identity = runtime_identity or {}
        with transaction(self.con):
            self.con.execute(
                "INSERT INTO controller_sessions(id,run_id,project_id,role,profile,subject_id,external_thread_id,config_hash,runtime_identity_json,state,created_at,closed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, run_id, project_id, role, profile, subject_id, external_thread_id, config_hash, canonical(identity), "ACTIVE", now(), None),
            )
        return AgentSession(session_id, run_id, project_id, role, profile, subject_id, external_thread_id, config_hash, identity, "ACTIVE")

    def close_session(self, session_id: str, *, uncertain: bool = False) -> None:
        with transaction(self.con):
            self.con.execute(
                "UPDATE controller_sessions SET state=?,closed_at=? WHERE id=? AND state='ACTIVE'",
                ("UNCERTAIN" if uncertain else "CLOSED", now(), session_id),
            )

    def begin_turn(
        self,
        session_id: str,
        prompt_kind: str,
        *,
        progress_before: str | None = None,
        packet: PromptPacket | None = None,
        dispatch_reason: DispatchReason | None = None,
        output_schema_bytes: int = 0,
    ) -> str:
        turn_id = new_id()
        with transaction(self.con):
            pending = self.con.execute(
                "SELECT id FROM controller_turns WHERE session_id=? AND (state IN ('DISPATCHING','RUNNING','UNCERTAIN') OR (state IN ('COMPLETED','FAILED') AND consumed_at IS NULL)) LIMIT 1",
                (session_id,),
            ).fetchone()
            if pending:
                raise RuntimeError(f"session has unresolved turn {pending['id']}")
            sequence = self.con.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM controller_turns WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            reason = dispatch_reason or (packet.dispatch_reason if packet else None)
            self.con.execute(
                "INSERT INTO controller_turns(id,session_id,sequence,state,prompt_kind,progress_before,dispatch_reason,prompt_builder_version,controller_payload_bytes,static_assignment_bytes,dynamic_state_bytes,correction_bytes,output_schema_bytes,dynamic_state_hash,context_hashes_json,started_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    turn_id, session_id, sequence, "DISPATCHING", prompt_kind, progress_before,
                    reason.value if reason else None, packet.prompt_builder_version if packet else "controller-prompt-v2",
                    packet.controller_payload_bytes if packet else 0,
                    packet.static_assignment_bytes if packet else 0,
                    packet.dynamic_state_bytes if packet else 0,
                    packet.correction_bytes if packet else 0,
                    output_schema_bytes or (packet.output_schema_bytes if packet else 0),
                    packet.dynamic_state_hash if packet else None,
                    canonical(packet.context_hashes if packet else {}), now(),
                ),
            )
            session = self.con.execute("SELECT run_id,role,subject_id FROM controller_sessions WHERE id=?", (session_id,)).fetchone()
            self._append_event_locked(
                session["run_id"], "SESSION", session_id, "TURN_DISPATCHED",
                reason.value if reason else None, turn_id,
                {"role": session["role"], "subject_id": session["subject_id"], "prompt_kind": prompt_kind},
            )
        return turn_id

    def acknowledge_turn(self, local_turn_id: str, handle: RuntimeTurnHandle) -> None:
        with transaction(self.con):
            owner = self.con.execute(
                "SELECT s.external_thread_id FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE t.id=?",
                (local_turn_id,),
            ).fetchone()
            if not owner or owner["external_thread_id"] != handle.thread_id:
                raise RuntimeError("turn acknowledgement thread does not match its session")
            cur = self.con.execute(
                "UPDATE controller_turns SET state='RUNNING',external_turn_id=? WHERE id=? AND state='DISPATCHING'",
                (handle.turn_id, local_turn_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("turn acknowledgement is stale")

    def complete_turn(self, local_turn_id: str, result: RuntimeTurnResult) -> None:
        with transaction(self.con):
            row = self.con.execute(
                "SELECT t.*,s.external_thread_id,s.role FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE t.id=?",
                (local_turn_id,),
            ).fetchone()
            if not row or row["state"] not in {"RUNNING", "COMPLETED"}:
                raise RuntimeError("completed turn is not reconcilable")
            if row["external_turn_id"] != result.handle.turn_id or row["external_thread_id"] != result.handle.thread_id:
                raise RuntimeError("completed turn identity does not match")
            payload = (
                canonical(result.structured_output)
                if row["role"] != "WORKER" and result.structured_output is not None
                else None
            )
            response_hash = hashlib.sha256((result.final_response or "").encode()).hexdigest() if result.final_response is not None else None
            before_json = canonical(result.cumulative_before.__dict__) if result.cumulative_before is not None else None
            after_json = canonical(result.cumulative_after.__dict__) if result.cumulative_after is not None else None
            usage_missing = result.usage_missing or result.usage_precision == UsagePrecision.MISSING
            if row["state"] == "COMPLETED":
                persisted = (
                    row["result_json"], row["response_hash"], row["input_tokens"], row["cached_input_tokens"],
                    row["output_tokens"], row["reasoning_tokens"], row["cache_write_input_tokens"], row["usage_precision"], row["usage_missing"],
                )
                incoming = (
                    payload, response_hash, result.usage.input_tokens, result.usage.cached_input_tokens,
                    result.usage.output_tokens, result.usage.reasoning_tokens, result.usage.cache_write_input_tokens,
                    result.usage_precision.value, int(usage_missing),
                )
                if persisted != incoming:
                    raise RuntimeError("completed turn result conflicts with the persisted result")
                return
            self.con.execute(
                "UPDATE controller_turns SET state='COMPLETED',result_json=?,final_response=NULL,response_hash=?,input_tokens=?,cached_input_tokens=?,cache_write_input_tokens=?,output_tokens=?,reasoning_tokens=?,usage_precision=?,usage_before_json=?,usage_after_json=?,exact_response_count=?,usage_missing=?,finished_at=? WHERE id=?",
                (payload, response_hash, result.usage.input_tokens, result.usage.cached_input_tokens,
                 result.usage.cache_write_input_tokens, result.usage.output_tokens, result.usage.reasoning_tokens,
                 result.usage_precision.value, before_json, after_json, result.exact_response_count,
                 int(usage_missing), now(), local_turn_id),
            )

    def record_usage_events(self, local_turn_id: str, events: tuple[dict[str, Any], ...]) -> None:
        """Legacy compatibility hook. New runs intentionally persist no raw provider events."""
        return None

    def latest_context_hashes(self, session_id: str) -> dict[str, str]:
        row = self.con.execute(
            "SELECT context_hashes_json FROM controller_turns WHERE session_id=? AND state='COMPLETED' ORDER BY sequence DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else {}

    def latest_cumulative_usage(self, session_id: str) -> Usage | None:
        row = self.con.execute(
            "SELECT usage_after_json FROM controller_turns "
            "WHERE session_id=? AND state='COMPLETED' AND usage_after_json IS NOT NULL "
            "ORDER BY sequence DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return Usage(**json.loads(row[0])) if row and row[0] else None

    def append_event(
        self, *, run_id: str, scope_type: str, scope_id: str, event_type: str,
        reason_code: str | None = None, local_turn_id: str | None = None,
        attributes: dict[str, Any] | None = None, transition_only: bool = False,
    ) -> bool:
        with transaction(self.con):
            if transition_only:
                last = self.con.execute(
                    "SELECT event_type,reason_code,small_attributes_json FROM controller_events WHERE run_id=? AND scope_type=? AND scope_id=? ORDER BY sequence DESC LIMIT 1",
                    (run_id, scope_type, scope_id),
                ).fetchone()
                attrs = canonical(attributes or {})
                if last and (last["event_type"], last["reason_code"], last["small_attributes_json"]) == (event_type, reason_code, attrs):
                    return False
            self._append_event_locked(run_id, scope_type, scope_id, event_type, reason_code, local_turn_id, attributes or {})
        return True

    def _append_event_locked(self, run_id, scope_type, scope_id, event_type, reason_code, local_turn_id, attributes):
        self.con.execute(
            "INSERT INTO controller_events(run_id,scope_type,scope_id,event_type,reason_code,local_turn_id,small_attributes_json,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
            (run_id, scope_type, scope_id, event_type, reason_code, local_turn_id, canonical(attributes), now()),
        )

    def fail_turn(self, local_turn_id: str, error: str, *, uncertain: bool) -> None:
        with transaction(self.con):
            self.con.execute(
                "UPDATE controller_turns SET state=?,error=?,finished_at=? WHERE id=? AND state IN ('DISPATCHING','RUNNING')",
                ("UNCERTAIN" if uncertain else "FAILED", error, now(), local_turn_id),
            )

    def consume_turn(self, local_turn_id: str, *, progress_after: str | None = None) -> None:
        with transaction(self.con):
            row = self.con.execute("SELECT state,progress_before FROM controller_turns WHERE id=?", (local_turn_id,)).fetchone()
            if not row or row["state"] not in {"COMPLETED", "FAILED"}:
                raise RuntimeError("only completed or failed turns can be consumed")
            progressed = None
            if progress_after is not None and row["progress_before"] is not None:
                progressed = int(progress_after != row["progress_before"])
            self.con.execute(
                "UPDATE controller_turns SET progress_after=COALESCE(?,progress_after),progressed=COALESCE(?,progressed),consumed_at=COALESCE(consumed_at,?) WHERE id=?",
                (progress_after, progressed, now(), local_turn_id),
            )

    def terminal_unconsumed_turn(self, session_id: str) -> TerminalTurn | None:
        row = self.con.execute(
            "SELECT t.*,s.external_thread_id FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE t.session_id=? AND t.state IN ('COMPLETED','FAILED') AND t.consumed_at IS NULL ORDER BY t.sequence LIMIT 1",
            (session_id,),
        ).fetchone()
        return self._terminal(row) if row else None

    def open_turns(self, *, project_id: str) -> list[OpenTurn]:
        rows = self.con.execute(
            "SELECT t.id,t.session_id,t.state,t.external_turn_id,s.external_thread_id FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.project_id=? AND t.state IN ('DISPATCHING','RUNNING','UNCERTAIN') ORDER BY t.started_at,t.id",
            (project_id,),
        ).fetchall()
        return [OpenTurn(r["id"], r["session_id"], r["external_thread_id"], r["state"], r["external_turn_id"]) for r in rows]

    def session_turn_count(self, session_id: str) -> int:
        return self.con.execute("SELECT COUNT(*) FROM controller_turns WHERE session_id=?", (session_id,)).fetchone()[0]

    def session_completed_turn_count(self, session_id: str) -> int:
        return self.con.execute(
            "SELECT COUNT(*) FROM controller_turns WHERE session_id=? AND state='COMPLETED'",
            (session_id,),
        ).fetchone()[0]

    def run_turn_count(self, *, run_id: str, roles: tuple[str, ...]) -> int:
        if not roles:
            return 0
        placeholders = ",".join("?" for _ in roles)
        return int(self.con.execute(
            f"SELECT COUNT(*) FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=? AND s.role IN ({placeholders})",
            (run_id, *roles),
        ).fetchone()[0])

    def missing_usage_count(self, *, run_id: str) -> int:
        return int(self.con.execute(
            "SELECT COUNT(*) FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=? AND t.state='COMPLETED' AND t.usage_missing=1",
            (run_id,),
        ).fetchone()[0])

    def consecutive_stalled_turns(self, session_id: str) -> int:
        rows = self.con.execute(
            "SELECT progressed FROM controller_turns WHERE session_id=? AND prompt_kind='worker' AND state='COMPLETED' AND consumed_at IS NOT NULL AND progressed IS NOT NULL ORDER BY sequence DESC",
            (session_id,),
        ).fetchall()
        count = 0
        for row in rows:
            if row["progressed"]:
                break
            count += 1
        return count

    def consecutive_failures(self, session_id: str) -> int:
        rows = self.con.execute(
            "SELECT state FROM controller_turns WHERE session_id=? AND consumed_at IS NOT NULL ORDER BY sequence DESC",
            (session_id,),
        ).fetchall()
        count = 0
        for row in rows:
            if row["state"] != "FAILED":
                break
            count += 1
        return count

    def usage(self, *, run_id: str) -> Usage:
        row = self.con.execute(
            "SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(cached_input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(reasoning_tokens),0),COALESCE(SUM(cache_write_input_tokens),0) FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=?",
            (run_id,),
        ).fetchone()
        return Usage(*map(int, row))

    def usage_report(self, *, run_id: str) -> dict[str, Any]:
        usage = self.usage(run_id=run_id)
        missing = self.missing_usage_count(run_id=run_id)
        turns = int(self.con.execute(
            "SELECT COUNT(*) FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=? AND t.state='COMPLETED'",
            (run_id,),
        ).fetchone()[0])
        precision = {row[0]: int(row[1]) for row in self.con.execute(
            "SELECT usage_precision,COUNT(*) FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=? AND t.state='COMPLETED' GROUP BY usage_precision ORDER BY usage_precision",
            (run_id,),
        )}
        return {**usage.__dict__, "completed_turns": turns, "missing_usage_turns": missing, "precision": precision, "complete": missing == 0}

    def add_targets(self, run_id: str, targets: list[dict[str, Any]]) -> None:
        with transaction(self.con):
            for target in targets:
                self.con.execute(
                    "INSERT OR IGNORE INTO controller_run_targets(run_id,task_id,wave,worker_profile,parallel_safe,write_surfaces_json,assignment_id,state) VALUES(?,?,?,?,?,?,NULL,'QUEUED')",
                    (run_id, target["task_id"], target["wave"], target["worker_profile"], int(target["parallel_safe"]), canonical(target["write_surfaces"])),
                )

    def targets(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.con.execute(
            "SELECT * FROM controller_run_targets WHERE run_id=? ORDER BY wave,task_id", (run_id,)
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["parallel_safe"] = bool(item["parallel_safe"])
            item["write_surfaces"] = json.loads(item.pop("write_surfaces_json"))
            result.append(item)
        return result

    def activate_target(self, run_id: str, task_id: str, assignment_id: str) -> None:
        with transaction(self.con):
            cur = self.con.execute(
                "UPDATE controller_run_targets SET assignment_id=?,state='ACTIVE' WHERE run_id=? AND task_id=? AND state='QUEUED'",
                (assignment_id, run_id, task_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("controller target activation is stale")

    def set_target_state(self, run_id: str, task_id: str, state: str, *, assignment_id: str | None = None) -> None:
        if state not in {"QUEUED", "ACTIVE", "INTEGRATED", "BLOCKED", "CANCELLED"}:
            raise ValueError(state)
        with transaction(self.con):
            self.con.execute(
                "UPDATE controller_run_targets SET state=?,assignment_id=COALESCE(?,assignment_id) WHERE run_id=? AND task_id=?",
                (state, assignment_id, run_id, task_id),
            )

    def reroute_target(self, run_id: str, task_id: str, *, profile: str, assignment_id: str | None = None) -> None:
        if profile not in {"routine", "complex"}:
            raise ValueError(profile)
        with transaction(self.con):
            self.con.execute(
                "UPDATE controller_run_targets SET worker_profile=?,assignment_id=?,state='QUEUED' WHERE run_id=? AND task_id=?",
                (profile, assignment_id, run_id, task_id),
            )

    def final_review(self, *, run_id: str, canonical_oid: str, plan_fingerprint: str) -> dict[str, Any] | None:
        row = self.con.execute(
            "SELECT * FROM controller_final_reviews WHERE run_id=? AND canonical_oid=? AND plan_fingerprint=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (run_id, canonical_oid, plan_fingerprint),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["verdict"] = json.loads(result.pop("verdict_json")) if result["verdict_json"] else None
        return result

    def create_final_review(self, *, run_id: str, canonical_oid: str, plan_fingerprint: str) -> str:
        review_id = new_id()
        with transaction(self.con):
            self.con.execute(
                "INSERT INTO controller_final_reviews(id,run_id,canonical_oid,plan_fingerprint,state,verdict_json,created_at,resolved_at) VALUES(?,?,?,?, 'PENDING',NULL,?,NULL)",
                (review_id, run_id, canonical_oid, plan_fingerprint, now()),
            )
        return review_id

    def resolve_final_review(self, review_id: str, *, state: str, verdict: dict[str, Any]) -> None:
        if state not in {"SATISFIED", "DEFECTS", "AMBIGUOUS", "INVALID"}:
            raise ValueError(state)
        with transaction(self.con):
            self.con.execute(
                "UPDATE controller_final_reviews SET state=?,verdict_json=?,resolved_at=? WHERE id=? AND state='PENDING'",
                (state, canonical(verdict), now(), review_id),
            )

    def grant_budget(self, *, run_id: str, kind: str, amount: int, reason: str, principal_id: str) -> str:
        if kind not in {"WORKER_TURNS", "REVIEWER_TURNS", "TASK_CREATOR_TURNS", "TOKENS", "ELAPSED_SECONDS"} or not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValueError("invalid controller budget grant")
        grant_id = new_id()
        with transaction(self.con):
            self.con.execute(
                "INSERT INTO controller_budget_grants VALUES(?,?,?,?,?,?,?)",
                (grant_id, run_id, kind, amount, reason, principal_id, now()),
            )
        return grant_id

    def granted_amount(self, *, run_id: str, kind: str) -> int:
        return int(self.con.execute(
            "SELECT COALESCE(SUM(amount),0) FROM controller_budget_grants WHERE run_id=? AND kind=?",
            (run_id, kind),
        ).fetchone()[0])

    def elapsed_seconds(self, *, run_id: str) -> int:
        row = self.con.execute("SELECT started_at FROM controller_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise RuntimeError("controller run was not found")
        started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
        return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))

    @staticmethod
    def _session(row) -> AgentSession:
        return AgentSession(row["id"], row["run_id"], row["project_id"], row["role"], row["profile"], row["subject_id"], row["external_thread_id"], row["config_hash"], json.loads(row["runtime_identity_json"]), row["state"])

    @staticmethod
    def _terminal(row) -> TerminalTurn:
        result = None
        if row["state"] == "COMPLETED":
            structured = json.loads(row["result_json"]) if row["result_json"] is not None else None
            result = RuntimeTurnResult(
                RuntimeTurnHandle(row["external_thread_id"], row["external_turn_id"]),
                structured_output=structured,
                final_response=row["final_response"],
                usage=Usage(row["input_tokens"], row["cached_input_tokens"], row["output_tokens"], row["reasoning_tokens"], row["cache_write_input_tokens"]),
                usage_missing=bool(row["usage_missing"]),
                usage_precision=UsagePrecision(row["usage_precision"]),
                cumulative_before=Usage(**json.loads(row["usage_before_json"])) if row["usage_before_json"] else None,
                cumulative_after=Usage(**json.loads(row["usage_after_json"])) if row["usage_after_json"] else None,
                exact_response_count=row["exact_response_count"],
            )
        return TerminalTurn(row["id"], row["session_id"], row["state"], row["prompt_kind"], row["external_thread_id"], row["external_turn_id"], result, row["error"], row["progress_before"], row["progress_after"], None if row["progressed"] is None else bool(row["progressed"]))
