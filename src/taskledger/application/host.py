from __future__ import annotations

import asyncio
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable

from taskledger import git
from taskledger.cli import home_for_repository
from taskledger.core import LedgerError
from taskledger.db import connect
from taskledger.notifications import ChangeNotice, bind_change_sink, unbind_change_sink
from taskledger.service import Service

from .contracts import ApplicationError, ConsoleSnapshot, OperationReceipt, OperationStatus, PauseRequestReceipt, Record, SnapshotVersion
from .live import LatestSnapshotFeed
from .queries import ConsoleQueries, record


class _RefreshSink:
    def __init__(self, schedule: Callable[[], None]): self.schedule = schedule
    def publish(self, notice: ChangeNotice) -> None: self.schedule()


class EngineHost:
    """Single-client bridge whose engine owns all mechanical resources."""
    def __init__(self, repository: str, *, project_id: str | None = None, token: str | None = None):
        self.repository, self.project_id, self.token = str(Path(repository).resolve()), project_id, token
        self.feed = LatestSnapshotFeed(); self.epoch = str(uuid.uuid4()); self.loop = None
        self.thread = threading.Thread(target=self._thread_main, name="taskledger-engine", daemon=False)
        self.ready = threading.Event(); self.closed = False; self._startup_error = None
        self._dirty = False; self._projection_queued = False; self._active: dict[str, Any] = {}
        self._operations: OrderedDict[str, tuple[Any, OperationStatus]] = OrderedDict()
        self.thread.start(); self.ready.wait(10)
        if self._startup_error: raise self._startup_error
        if not self.ready.is_set(): raise RuntimeError("Taskledger engine did not start")

    def _thread_main(self):
        self.loop = asyncio.new_event_loop(); asyncio.set_event_loop(self.loop)
        try:
            info = git.inspect(self.repository); home = home_for_repository(info, create=False)
            if not (home / "taskledger.sqlite3").is_file():
                self.service = None; self.project = None; self.principal = None; self.queries = None
                self.feed.publish(ConsoleSnapshot(1,SnapshotVersion(self.epoch,1),"",record(id=None,repository_root=self.repository,effective_phase="UNINITIALIZED"),None,None,(),(),record(accounting_quality="UNAVAILABLE"),(),()))
                self.ready.set(); self.loop.run_forever(); self.loop.run_until_complete(self.loop.shutdown_asyncgens()); self.loop.close(); return
            self.service = Service(connect(home), home)
            self.project, self.principal = self.service.auth_orchestrator(self.project_id, self.token)
            self.queries = ConsoleQueries(self.service, self.project, self.epoch, lambda run_id: run_id in self._active)
            bind_change_sink(self.service.con, _RefreshSink(self._schedule_projection))
            self.feed.publish(self.queries.snapshot())
        except BaseException as exc:
            self._startup_error = exc; self.ready.set(); self.loop.close(); return
        self.ready.set()
        try: self.loop.run_forever()
        finally:
            unbind_change_sink(self.service.con); self.service.con.close()
            self.loop.run_until_complete(self.loop.shutdown_asyncgens()); self.loop.close()

    def _schedule_projection(self):
        self._dirty = True
        if not self._projection_queued and self.loop is not None:
            self._projection_queued = True; self.loop.call_soon(self._project)

    def _project(self):
        self._projection_queued = False
        if not self._dirty: return
        self._dirty = False
        try: self.feed.publish(self.queries.snapshot())
        except Exception: pass

    def submit(self, function: Callable[..., Any], *args: Any) -> Future:
        if self.closed or self.loop is None: raise RuntimeError("ENGINE_CLOSING")
        async def invoke(): return function(*args)
        return asyncio.run_coroutine_threadsafe(invoke(), self.loop)

    def submit_coro(self, coroutine) -> Future:
        if self.closed or self.loop is None: raise RuntimeError("ENGINE_CLOSING")
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def current_snapshot(self): return self.feed.latest
    def open_feed(self): return self.feed.attach(asyncio.get_running_loop())
    async def refresh_snapshot(self):
        await asyncio.wrap_future(self.submit(self._refresh)); return self.feed.latest
    def _refresh(self):
        if self.queries is not None: self.feed.publish(self.queries.snapshot())
    async def task_detail(self, task_id: str):
        if self.queries is None: raise LedgerError("PROJECT_REQUIRED", "Taskledger is not initialized for this repository.")
        return await asyncio.wrap_future(self.submit(self.queries.task_detail, task_id))
    async def preparation_detail(self, preparation_id: str):
        if self.queries is None: raise LedgerError("PROJECT_REQUIRED", "Taskledger is not initialized for this repository.")
        return await asyncio.wrap_future(self.submit(self.queries.preparation_detail, preparation_id))
    async def history_page(self, cursor, limit=100):
        if self.queries is None: raise LedgerError("PROJECT_REQUIRED", "Taskledger is not initialized for this repository.")
        return await asyncio.wrap_future(self.submit(self.queries.history_page, cursor, limit))

    async def run_report(self, run_id: str):
        if self.queries is None: raise LedgerError("PROJECT_REQUIRED", "Taskledger is not initialized for this repository.")
        from taskledger.controller.reporting import controller_report
        run = await asyncio.wrap_future(self.submit(lambda: self.service.con.execute("SELECT project_id FROM controller_runs WHERE id=?",(run_id,)).fetchone()))
        if not run or run["project_id"] != self.project["id"]: raise LedgerError("INVALID_REQUEST", "Controller run was not found.")
        return await asyncio.wrap_future(self.submit_coro(controller_report(self.service.con,run_id,include_provider_detail=False)))

    async def mutate(self, request_id: str, kind: str, payload: dict[str, Any]) -> OperationReceipt:
        return await asyncio.shield(asyncio.wrap_future(self.submit(self._mutate, request_id, kind, payload)))

    async def answer_question(self, request_id: str, **payload): return await self.mutate(request_id,"answer_question",payload)
    async def resolve_blocker(self, request_id: str, **payload): return await self.mutate(request_id,"resolve_blocker",payload)
    async def extend_budget(self, request_id: str, **payload): return await self.mutate(request_id,"extend_budget",payload)
    async def prepare_project(self, request_id: str, **payload): return await self.mutate(request_id,"prepare_project",payload)
    async def start_prepared_project(self, request_id: str, **payload): return await self.mutate(request_id,"start_prepared_project",payload)
    async def resume_run(self, request_id: str, **payload): return await self.mutate(request_id,"resume_run",payload)

    def _mutate(self, request_id, kind, payload):
        fingerprint = (kind, repr(sorted(payload.items())))
        existing = self._operations.get(request_id)
        if existing:
            if existing[0] != fingerprint: return OperationReceipt(request_id, "REJECTED", error=ApplicationError("REQUEST_ID_REUSED", "Request ID was already used with different inputs."))
            return existing[1].receipt
        try:
            if kind == "answer_question": result = self.service.answer_question(self.project, self.principal, payload)
            elif kind == "resolve_blocker": result = self.service.resolve_blocker(self.project, self.principal, payload)
            elif kind == "extend_budget":
                from taskledger.controller.journal import Journal
                run_id = payload["run_id"]
                run = Journal(self.service.con,self.service.home).run(run_id)
                if not run or run["project_id"] != self.project["id"]: raise LedgerError("INVALID_REQUEST", "Controller run was not found.")
                grant = Journal(self.service.con,self.service.home).grant_budget(run_id=run_id,kind=payload["kind"],amount=payload["amount"],reason=payload["reason"],principal_id=self.principal["id"])
                result = {"grant_id": grant}
            else: raise LedgerError("UNSUPPORTED_OPERATION", f"Unsupported console operation: {kind}")
            receipt = OperationReceipt(request_id, "APPLIED", run_id=payload.get("run_id"))
            status = OperationStatus(request_id,"SUCCEEDED",receipt,record(**result))
        except LedgerError as exc:
            receipt = OperationReceipt(request_id,"REJECTED",error=ApplicationError(exc.code,exc.message))
            status = OperationStatus(request_id,"FAILED",receipt,error=receipt.error)
        self._operations[request_id] = (fingerprint,status)
        while len(self._operations) > 256:
            self._operations.popitem(last=False)
        return receipt

    async def operation_status(self, operation_id):
        def lookup(): return self._operations.get(operation_id, (None, OperationStatus(operation_id,"UNKNOWN_OPERATION",None)))[1]
        return await asyncio.wrap_future(self.submit(lookup))

    async def request_pause(self, lifecycle_id: str, reason: str = "USER_INTERRUPTED") -> PauseRequestReceipt:
        # Runs admitted by this host install a pause callback in _active.
        callback = self._active.get(lifecycle_id)
        if callback is None: return PauseRequestReceipt(lifecycle_id, "NOT_RUNNING")
        callback(reason); return PauseRequestReceipt(lifecycle_id, "REQUESTED")

    async def close(self):
        if self.closed: return
        self.closed = True
        if self.feed.reader: self.feed.reader.close()
        if self.loop: self.loop.call_soon_threadsafe(self.loop.stop)
        await asyncio.to_thread(self.thread.join, 10)
        if self.thread.is_alive(): raise RuntimeError("engine shutdown is waiting for a safe point")
