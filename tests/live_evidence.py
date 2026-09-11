from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from taskledger.controller.reporting import controller_report


class LiveEvidence:
    """Compact, local evidence for a paid validation attempt.

    This intentionally records controller/provider identities and accounting,
    never prompts, transcripts, commands, patches, or provider event bodies.
    """

    def __init__(
        self,
        *,
        directory: Path,
        validation_id: str,
        source_root: Path,
        model: str,
        effort: str,
        configuration: dict[str, Any],
        attempt_id: str | None = None,
    ):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._enforce_cap(configuration.get("global_validation_cap"))
        self.attempt_id = attempt_id or uuid.uuid4().hex
        self.path = self.directory / f"{validation_id}-{self.attempt_id}.json"
        self._thread_roles: dict[str, dict[str, str]] = {}
        self.document: dict[str, Any] = {
            "test_validation_id": validation_id,
            "source_revision": self._source_revision(source_root),
            "run_id": None,
            "attempt_id": self.attempt_id,
            "sessions": [],
            "turns": [],
            "model": model,
            "effort": effort,
            "configuration_identity": configuration,
            "usage_allocations": [],
            "dispatch_reasons": [],
            "outcome": "REGISTERED",
            "controller_events": [],
            "report": None,
            "report_failure": None,
            "failure": None,
        }
        self._write()

    def _enforce_cap(self, cap: Any) -> None:
        if not isinstance(cap, dict):
            return
        documents = []
        for path in self.directory.glob("*.json"):
            try:
                documents.append(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        allocations = {
            (row["thread_id"], row["turn_id"]): row
            for document in documents
            for row in document.get("usage_allocations", [])
        }
        admission = sum(
            int(row.get("input_tokens", 0)) + int(row.get("output_tokens", 0))
            for row in allocations.values()
        )
        elapsed_ms = sum(
            int(((document.get("report") or {}).get("scheduler") or {}).get("gross_elapsed_ms") or 0)
            for document in documents
        )
        maximum_attempts = 1 + int(cap.get("corrected_reruns", 0))
        if (
            len(documents) >= maximum_attempts
            or len(allocations) >= int(cap.get("paid_turns", 0))
            or admission >= int(cap.get("admission_tokens", 0))
            or elapsed_ms >= int(cap.get("elapsed_seconds", 0)) * 1000
        ):
            raise RuntimeError("global paid validation allowance is exhausted")

    @staticmethod
    def _source_revision(root: Path) -> dict[str, str | bool]:
        try:
            head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            diff = subprocess.run(
                ["git", "-C", str(root), "diff", "--binary", "HEAD"],
                check=True,
                capture_output=True,
            ).stdout
            return {
                "head_oid": head,
                "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
                "tracked_dirty": bool(diff),
            }
        except (OSError, subprocess.SubprocessError):
            return {"head_oid": "UNAVAILABLE", "tracked_diff_sha256": "UNAVAILABLE", "tracked_dirty": False}

    def _write(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.document, indent=2, sort_keys=True) + "\n")
        temporary.replace(self.path)

    def session(self, *, role: str, subject_id: str, thread_id: str, resumed: bool) -> None:
        self._thread_roles[thread_id] = {"role": role, "subject_id": subject_id}
        identity = (role, subject_id, thread_id)
        if not any((row["role"], row["subject_id"], row["thread_id"]) == identity for row in self.document["sessions"]):
            self.document["sessions"].append({
                "role": role,
                "subject_id": subject_id,
                "thread_id": thread_id,
                "resumed": resumed,
            })
        elif resumed:
            next(row for row in self.document["sessions"] if (row["role"], row["subject_id"], row["thread_id"]) == identity)["resumed"] = True
        self._write()

    def turn_started(self, *, thread_id: str, turn_id: str) -> None:
        meta = self._thread_roles.get(thread_id, {})
        identity = (thread_id, turn_id)
        if not any((row["thread_id"], row["turn_id"]) == identity for row in self.document["turns"]):
            self.document["turns"].append({
                "thread_id": thread_id,
                "turn_id": turn_id,
                "role": meta.get("role"),
                "subject_id": meta.get("subject_id"),
                "outcome": "ACKNOWLEDGED",
            })
        self._write()

    def turn_result(self, result, *, outcome: str) -> None:
        identity = (result.handle.thread_id, result.handle.turn_id)
        for row in self.document["turns"]:
            if (row["thread_id"], row["turn_id"]) == identity:
                row["outcome"] = outcome
                break
        allocation = {
            "thread_id": result.handle.thread_id,
            "turn_id": result.handle.turn_id,
            **result.usage.__dict__,
            "precision": result.usage_precision.value,
            "complete": not result.usage_missing,
        }
        self.document["usage_allocations"] = [
            row for row in self.document["usage_allocations"]
            if (row["thread_id"], row["turn_id"]) != identity
        ] + [allocation]
        self._write()

    async def finalize(
        self,
        *,
        con=None,
        run_id: str | None = None,
        outcome: str,
        failure: BaseException | None = None,
        provider_loader=None,
    ) -> None:
        self.document["run_id"] = run_id
        self.document["outcome"] = outcome
        if failure is not None:
            self.document["failure"] = {"type": type(failure).__name__, "message": str(failure)}
        if con is not None and run_id is not None:
            try:
                local = await controller_report(con, run_id, include_provider_detail=False)
                self.document["report"] = local
                controller_sessions = {
                    row["external_thread_id"]: row["id"]
                    for row in con.execute(
                        "SELECT id,external_thread_id FROM controller_sessions WHERE run_id=?",
                        (run_id,),
                    )
                    if row["external_thread_id"]
                }
                for session in self.document["sessions"]:
                    session["session_id"] = controller_sessions.get(session["thread_id"])
                self.document["dispatch_reasons"] = sorted({
                    row[0] for row in con.execute(
                        "SELECT t.dispatch_reason FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=? AND t.dispatch_reason IS NOT NULL",
                        (run_id,),
                    )
                })
                self.document["controller_events"] = [
                    {
                        "sequence": row["sequence"],
                        "scope_type": row["scope_type"],
                        "scope_id": row["scope_id"],
                        "event_type": row["event_type"],
                        "reason_code": row["reason_code"],
                    }
                    for row in con.execute(
                        "SELECT sequence,scope_type,scope_id,event_type,reason_code FROM controller_events WHERE run_id=? ORDER BY sequence",
                        (run_id,),
                    )
                ]
                self._write()
                if provider_loader is not None:
                    self.document["report"] = await controller_report(
                        con,
                        run_id,
                        include_provider_detail=True,
                        provider_loader=provider_loader,
                    )
            except Exception as exc:
                self.document["report_failure"] = {"type": type(exc).__name__, "message": str(exc)}
        self._write()

    @classmethod
    def print_rollup(cls, directory: Path) -> None:
        documents = []
        for path in sorted(directory.glob("*.json")):
            try:
                documents.append(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        allocations: dict[tuple[str, str], dict[str, Any]] = {}
        for document in documents:
            for row in document.get("usage_allocations", []):
                allocations[(row["thread_id"], row["turn_id"])] = row
        totals = {
            key: sum(int(row.get(key, 0)) for row in allocations.values())
            for key in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_tokens")
        }
        totals["admission_tokens"] = totals["input_tokens"] + totals["output_tokens"]
        print("TASKLEDGER_PAID_VALIDATION_ROLLUP " + json.dumps({
            "attempts": [
                {"attempt_id": row.get("attempt_id"), "validation_id": row.get("test_validation_id"), "outcome": row.get("outcome")}
                for row in documents
            ],
            "deduplicated_turns": len(allocations),
            "totals": totals,
        }, sort_keys=True))


class EvidenceRuntime:
    """Passive runtime observer; it does not alter results, timing, or state."""

    def __init__(self, runtime, evidence: LiveEvidence, *, on_result=None):
        object.__setattr__(self, "runtime", runtime)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "on_result", on_result)

    def __getattr__(self, name):
        return getattr(self.runtime, name)

    def __setattr__(self, name, value):
        if name in {"runtime", "evidence", "on_result"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self.runtime, name, value)

    async def start_session(self, **kwargs):
        session = await self.runtime.start_session(**kwargs)
        self.evidence.session(
            role=kwargs["role"].value,
            subject_id=kwargs["subject_id"],
            thread_id=session.thread_id,
            resumed=False,
        )
        return session

    async def resume_session(self, **kwargs):
        session = await self.runtime.resume_session(**kwargs)
        self.evidence.session(
            role=kwargs["role"].value,
            subject_id=kwargs["subject_id"],
            thread_id=session.thread_id,
            resumed=True,
        )
        return session

    async def start_turn(self, **kwargs):
        handle = await self.runtime.start_turn(**kwargs)
        self.evidence.turn_started(thread_id=handle.thread_id, turn_id=handle.turn_id)
        return handle

    async def wait_turn(self, handle):
        result = await self.runtime.wait_turn(handle)
        self.evidence.turn_result(result, outcome="COMPLETED")
        if self.on_result is not None:
            value = self.on_result(result, self.evidence._thread_roles.get(result.handle.thread_id, {}))
            if hasattr(value, "__await__"):
                await value
        return result

    async def inspect_turn(self, handle):
        inspection = await self.runtime.inspect_turn(handle)
        if inspection.result is not None:
            self.evidence.turn_result(inspection.result, outcome=inspection.state)
        return inspection
