from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from taskledger import git
from taskledger.core import LedgerError, canonical
from taskledger.db import connect, connect_existing
from taskledger.notifications import ChangeNotice, bind_change_sink, unbind_change_sink
from taskledger.repository import home_for_repository
from taskledger.service import Service

from .contracts import ApplicationError, ConsoleSnapshot, OperationReceipt, OperationStatus, PauseRequestReceipt, Record, SnapshotVersion
from .live import LatestSnapshotFeed
from .queries import ConsoleQueries, record


class _RefreshSink:
    def __init__(self, schedule: Callable[[], None]): self.schedule = schedule
    def publish(self, notice: ChangeNotice) -> None: self.schedule()


class EngineHost:
    """Single-client bridge whose engine owns all mechanical resources."""
    def __init__(self, repository: str, *, project_id: str | None = None, token: str | None = None, runtime_factory=None):
        self.repository, self.project_id, self.token = str(Path(repository).resolve()), project_id, token
        self.runtime_factory = runtime_factory
        self.feed = LatestSnapshotFeed(); self.epoch = str(uuid.uuid4()); self.loop = None
        self.thread = threading.Thread(target=self._thread_main, name="taskledger-engine", daemon=False)
        self.ready = threading.Event(); self._startup_error = None
        self.service = self.project = self.principal = self.queries = None
        self._state_lock = threading.Lock(); self._state = "OPEN"; self._shutdown_future = None
        self._dirty = False; self._projection_queued = False; self._active: dict[str, Any] = {}
        self._active_lock = threading.Lock()
        self._pause_requested: set[str] = set()
        self._transient_operation = None
        self._lifecycle_operation_id = None
        self._active_operations: dict[str, tuple[Any, OperationStatus, asyncio.Task[Any]]] = {}
        self._completed_operations: OrderedDict[str, tuple[Any, OperationStatus]] = OrderedDict()
        self.thread.start(); self.ready.wait(10)
        if self._startup_error:
            self.thread.join()
            raise self._startup_error
        if not self.ready.is_set():
            # Do not leave a non-daemon owner thread behind after a failed
            # startup handshake.  The thread observes this stop immediately
            # once it reaches the loop, and construction waits for its exit.
            self._set_state("CLOSING")
            if self.loop is not None:
                self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join()
            self._set_state("CLOSED")
            raise RuntimeError("Taskledger engine did not start")

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._state == "CLOSED"

    @property
    def lifecycle_state(self) -> str:
        with self._state_lock:
            return self._state

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state

    def _is_open(self) -> bool:
        with self._state_lock:
            return self._state == "OPEN"

    def _thread_main(self):
        self.loop = asyncio.new_event_loop(); asyncio.set_event_loop(self.loop)
        try:
            info = git.inspect(self.repository); home = home_for_repository(info, create=False)
            if not (home / "taskledger.sqlite3").is_file():
                self.feed.publish(ConsoleSnapshot(1,SnapshotVersion(self.epoch,1),"",record(id=None,repository_root=self.repository,effective_phase="UNINITIALIZED"),None,None,(),(),record(accounting_quality="UNAVAILABLE"),(),()))
            else:
                self._adopt_service(Service(connect_existing(home), home))
        except BaseException as exc:
            self._startup_error = exc; self.ready.set()
            self._set_state("CLOSED")
            self.loop.close(); return
        self.ready.set()
        try: self.loop.run_forever()
        finally:
            try:
                if self.service is not None:
                    unbind_change_sink(self.service.con)
                    self.service.con.close()
            finally:
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
                self.loop.close()
                self._set_state("CLOSED")

    def _adopt_service(self, service, *, project_id: str | None = None):
        """Adopt a fully constructed service, or leave the host untouched.

        A failed auth/projection must not expose a closed connection through
        ``self.service``.  This method owns ``service`` until the final state
        assignment succeeds.
        """
        bound = False
        try:
            project, principal = service.auth_orchestrator(project_id or self.project_id, self.token)
            queries = ConsoleQueries(service, project, self.epoch, self._owns_run, self._is_pause_requested)
            if self.feed.latest is not None:
                queries.revision = self.feed.latest.version.revision
            bind_change_sink(service.con, _RefreshSink(self._notice_committed)); bound = True
            snapshot = queries.snapshot()
            # Publishing is also part of adoption: if a feed implementation
            # rejects this snapshot, retain neither its service nor its sink.
            self.feed.publish(snapshot)
        except BaseException:
            if bound:
                unbind_change_sink(service.con)
            service.con.close()
            raise
        self.service, self.project, self.principal, self.queries = service, project, principal, queries

    def _owns_run(self, run_id):
        with self._active_lock: return run_id in self._active

    def _is_pause_requested(self, run_id):
        with self._active_lock: return run_id in self._pause_requested

    def _ensure_initialized_for_prepare(self):
        if self.service is not None:
            return
        info = git.inspect(self.repository)
        if not info["has_commits"]:
            raise LedgerError("INITIAL_COMMIT_REQUIRED", "The repository needs an initial commit before preparation.")
        if not git.clean(info["root"]):
            raise LedgerError("CANONICAL_WORKTREE_DIRTY", "The canonical worktree must be clean before preparation.")
        if not os.environ.get("TASKLEDGER_HOME") and not git.ignored(info["root"], ".taskledger/"):
            raise LedgerError("LEDGER_DIRECTORY_NOT_IGNORED", "Add .taskledger/ to this repository's ignore rules before preparation.")
        home = home_for_repository(info, create=True)
        service = Service(connect(home), home)
        try:
            initialized = service.init(info["root"], info["branch"])
            selected_project_id = self.project_id or initialized["project_id"]
            self._adopt_service(service, project_id=selected_project_id)
            self.project_id = selected_project_id
        except BaseException:
            # _adopt_service owns and closes a failed candidate.  A failure
            # before that transfer (for example init itself) remains ours.
            if self.service is not service:
                try: service.con.close()
                except Exception: pass
            raise

    def _notice_committed(self) -> None:
        """Cross-thread committed-change ingress; never blocks a transaction."""
        loop = self.loop
        if loop is not None and self.lifecycle_state != "CLOSED":
            try:
                loop.call_soon_threadsafe(self._schedule_projection)
            except RuntimeError:
                pass

    def _schedule_projection(self):
        self._dirty = True
        if not self._projection_queued and self.loop is not None:
            self._projection_queued = True; self.loop.call_soon(self._project)

    def _project(self):
        self._projection_queued = False
        if not self._dirty: return
        self._dirty = False
        try:
            self.feed.publish(self.queries.snapshot(operation=self._operation_record()))
        except Exception as exc:
            self._publish_observation_error(exc)

    def _operation_record(self):
        if self._transient_operation is not None:
            return self._transient_operation
        if not self._active_operations:
            return None
        operation_id, (fingerprint, status, _task) = next(iter(self._active_operations.items()))
        with self._active_lock:
            active_ids = tuple(self._active)
            pause_requested = frozenset(self._pause_requested)
        lifecycle_id = next((key for key in active_ids if key != operation_id), operation_id)
        return record(operation_id=operation_id, kind=fingerprint[0], phase=status.phase,
            lifecycle_id=lifecycle_id, pause_requested=lifecycle_id in pause_requested)

    def _publish_observation_error(self, exc):
        latest = self.feed.latest
        if latest is None:
            return
        fields = dict(latest.project.fields)
        fields.update(cache_health="DEGRADED", observation_error=exc.__class__.__name__)
        if self.queries is not None:
            self.queries.revision = max(self.queries.revision, latest.version.revision + 1)
        self.feed.publish(replace(latest,
            version=SnapshotVersion(self.epoch, latest.version.revision + 1), project=record(**fields)))

    def _set_transient_operation(self, operation_id, kind, phase):
        self._transient_operation = record(operation_id=operation_id, kind=kind, phase=phase)
        if self.queries is not None:
            try: self.feed.publish(self.queries.snapshot(operation=self._transient_operation))
            except Exception as exc: self._publish_observation_error(exc)
        elif self.feed.latest is not None:
            latest = self.feed.latest
            self.feed.publish(replace(latest, version=SnapshotVersion(self.epoch, latest.version.revision + 1),
                operation=self._transient_operation))

    def submit(self, function: Callable[..., Any], *args: Any) -> Future:
        if not self._is_open() or self.loop is None: raise RuntimeError("ENGINE_CLOSING")
        async def invoke(): return function(*args)
        return asyncio.run_coroutine_threadsafe(invoke(), self.loop)

    def submit_async(self, factory: Callable[[], Any]) -> Future:
        """Invoke an async factory on the engine loop, never a caller-built coroutine."""
        if not self._is_open() or self.loop is None: raise RuntimeError("ENGINE_CLOSING")
        async def invoke(): return await factory()
        return asyncio.run_coroutine_threadsafe(invoke(), self.loop)

    def current_snapshot(self): return self.feed.latest
    def open_feed(self): return self.feed.attach(asyncio.get_running_loop())
    async def refresh_snapshot(self):
        await asyncio.wrap_future(self.submit(self._refresh)); return self.feed.latest
    def _refresh(self):
        if self.queries is not None:
            try: self.feed.publish(self.queries.snapshot(operation=self._operation_record()))
            except Exception as exc: self._publish_observation_error(exc)
    def _query(self, method: str, *args):
        if self.queries is None:
            raise LedgerError("PROJECT_REQUIRED", "Taskledger is not initialized for this repository.")
        return getattr(self.queries, method)(*args)

    async def task_detail(self, task_id: str):
        return await asyncio.wrap_future(self.submit(self._query, "task_detail", task_id))
    async def task_page(self, offset: int = 0, limit: int = 200):
        return await asyncio.wrap_future(self.submit(self._query, "task_page", offset, limit))
    async def preparation_detail(self, preparation_id: str):
        return await asyncio.wrap_future(self.submit(self._query, "preparation_detail", preparation_id))
    async def history_page(self, cursor, limit=100):
        return await asyncio.wrap_future(self.submit(self._query, "history_page", cursor, limit))

    async def run_report(self, run_id: str):
        from taskledger.controller.reporting import controller_report
        async def build():
            if self.queries is None or self.service is None or self.project is None:
                raise LedgerError("PROJECT_REQUIRED", "Taskledger is not initialized for this repository.")
            run = self.service.con.execute(
                "SELECT project_id FROM controller_runs WHERE id=?", (run_id,)
            ).fetchone()
            if not run or run["project_id"] != self.project["id"]:
                raise LedgerError("INVALID_REQUEST", "Controller run was not found.")
            report = await controller_report(self.service.con,run_id,include_provider_detail=False)
            return record(run_id=run_id, **report)
        return await asyncio.wrap_future(self.submit_async(build))

    async def mutate(self, request_id: str, kind: str, payload: dict[str, Any]) -> OperationReceipt:
        payload = json.loads(canonical(payload))
        return await asyncio.shield(asyncio.wrap_future(self.submit(self._mutate, request_id, kind, payload)))

    async def answer_question(self, request_id: str, **payload): return await self.mutate(request_id,"answer_question",payload)
    async def resolve_blocker(self, request_id: str, **payload): return await self.mutate(request_id,"resolve_blocker",payload)
    async def extend_budget(self, request_id: str, **payload): return await self.mutate(request_id,"extend_budget",payload)
    async def prepare_project(self, request_id: str, **payload):
        payload = json.loads(canonical(payload))
        return await asyncio.shield(asyncio.wrap_future(self.submit(self._admit_prepare, request_id, payload)))

    def _admit_prepare(self, request_id, payload):
        from .commands import prepare_project
        from taskledger.controller.initial_planning import parse_preparation_request
        fingerprint = ("prepare_project", canonical(payload))
        existing = self._active_operations.get(request_id) or self._completed_operations.get(request_id)
        if existing:
            if existing[0] != fingerprint:
                return OperationReceipt(request_id, "REJECTED", error=ApplicationError("REQUEST_ID_REUSED", "Request ID was already used with different inputs."))
            return existing[1].receipt
        if self._lifecycle_operation_id is not None:
            return self._record_rejection(request_id, fingerprint, "ENGINE_BUSY", "Another lifecycle operation is active.")
        if len(self._active_operations) >= 32:
            return self._record_rejection(request_id, fingerprint, "OPERATION_CAPACITY", "Too many unresolved operations are active.")
        try:
            parse_preparation_request(payload)
        except LedgerError as exc:
            return self._record_rejection(request_id, fingerprint, exc.code, exc.message)
        stop = threading.Event()
        receipt = OperationReceipt(request_id, "ACCEPTED")
        self._lifecycle_operation_id = request_id
        with self._active_lock:
            self._active[request_id] = lambda _reason="USER_INTERRUPTED": stop.set()

        async def work():
            try:
                self._set_transient_operation(request_id, "prepare_project", "INITIALIZING")
                try: self._ensure_initialized_for_prepare()
                finally: self._transient_operation = None
                return await prepare_project(self.service, self.project, self.principal, payload,
                    runtime_factory=self.runtime_factory, stop_requested=stop.is_set)
            finally:
                with self._active_lock:
                    self._active.pop(request_id, None)
                    self._pause_requested.discard(request_id)
                if self._lifecycle_operation_id == request_id: self._lifecycle_operation_id = None
        return self._admit_owned_operation(request_id, fingerprint, receipt, work)

    async def start_prepared_project(self, request_id: str, **payload):
        payload = json.loads(canonical(payload))
        return await asyncio.shield(asyncio.wrap_future(self.submit(self._admit_start, request_id, payload)))

    def _admit_start(self, request_id, payload):
        from .commands import admit_prepared_start
        fingerprint = ("start_prepared_project", canonical(payload))
        existing = self._active_operations.get(request_id) or self._completed_operations.get(request_id)
        if existing:
            if existing[0] != fingerprint:
                return OperationReceipt(request_id, "REJECTED", error=ApplicationError("REQUEST_ID_REUSED", "Request ID was already used with different inputs."))
            return existing[1].receipt
        if self._lifecycle_operation_id is not None:
            return self._record_rejection(request_id, fingerprint, "ENGINE_BUSY", "Another lifecycle operation is active.")
        if len(self._active_operations) >= 32:
            return self._record_rejection(request_id, fingerprint, "OPERATION_CAPACITY", "Too many unresolved operations are active.")
        try:
            if self.service is None:
                raise LedgerError("PROJECT_REQUIRED", "Taskledger is not initialized for this repository.")
            self._set_transient_operation(request_id, "start_prepared_project", "ADMITTING")
            try: admitted = admit_prepared_start(self.service, self.project, self.principal, payload, runtime_factory=self.runtime_factory)
            finally: self._transient_operation = None
        except (LedgerError, RuntimeError) as exc:
            code = exc.code if isinstance(exc, LedgerError) else "INVALID_REQUEST"
            message = exc.message if isinstance(exc, LedgerError) else "Console operation input is invalid."
            return self._record_rejection(request_id, fingerprint, code, message)
        receipt = OperationReceipt(request_id, "ACCEPTED", run_id=admitted.run_id, preparation_id=admitted.preparation_id)
        self._lifecycle_operation_id = request_id
        with self._active_lock: self._active[admitted.lifecycle_id] = admitted.owner.request_stop

        async def work():
            try:
                return asdict(await admitted.owner.run())
            finally:
                with self._active_lock: self._active.pop(admitted.lifecycle_id, None)
                with self._active_lock: self._pause_requested.discard(admitted.lifecycle_id)
                if self._lifecycle_operation_id == request_id: self._lifecycle_operation_id = None
        from dataclasses import asdict
        return self._admit_owned_operation(request_id, fingerprint, receipt, work)

    async def resume_run(self, request_id: str, **payload):
        payload = json.loads(canonical(payload))
        return await asyncio.shield(asyncio.wrap_future(self.submit(self._admit_resume, request_id, payload)))

    def _admit_resume(self, request_id, payload):
        from dataclasses import asdict
        from .commands import admit_resume
        fingerprint = ("resume_run", canonical(payload))
        existing = self._active_operations.get(request_id) or self._completed_operations.get(request_id)
        if existing:
            if existing[0] != fingerprint:
                return OperationReceipt(request_id, "REJECTED", error=ApplicationError("REQUEST_ID_REUSED", "Request ID was already used with different inputs."))
            return existing[1].receipt
        if self._lifecycle_operation_id is not None:
            return self._record_rejection(request_id, fingerprint, "ENGINE_BUSY", "Another lifecycle operation is active.")
        if len(self._active_operations) >= 32:
            return self._record_rejection(request_id, fingerprint, "OPERATION_CAPACITY", "Too many unresolved operations are active.")
        try:
            if self.service is None:
                raise LedgerError("PROJECT_REQUIRED", "Taskledger is not initialized for this repository.")
            self._set_transient_operation(request_id, "resume_run", "ADMITTING")
            try: admitted = admit_resume(self.service, self.project, self.principal, payload, runtime_factory=self.runtime_factory)
            finally: self._transient_operation = None
        except (LedgerError, RuntimeError) as exc:
            code = exc.code if isinstance(exc, LedgerError) else "INVALID_REQUEST"
            message = exc.message if isinstance(exc, LedgerError) else "Console operation input is invalid."
            return self._record_rejection(request_id, fingerprint, code, message)
        receipt = OperationReceipt(request_id, "ACCEPTED", run_id=admitted.run_id)
        self._lifecycle_operation_id = request_id
        with self._active_lock: self._active[admitted.lifecycle_id] = admitted.owner.request_stop
        async def work():
            try:
                return asdict(await admitted.owner.run())
            finally:
                with self._active_lock: self._active.pop(admitted.lifecycle_id, None)
                with self._active_lock: self._pause_requested.discard(admitted.lifecycle_id)
                if self._lifecycle_operation_id == request_id: self._lifecycle_operation_id = None
        return self._admit_owned_operation(request_id, fingerprint, receipt, work)

    def _record_rejection(self, request_id, fingerprint, code, message):
        receipt = OperationReceipt(request_id, "REJECTED", error=ApplicationError(code, message))
        self._completed_operations[request_id] = (fingerprint,
            OperationStatus(request_id, "FAILED", receipt, error=receipt.error))
        while len(self._completed_operations) > 256:
            self._completed_operations.popitem(last=False)
        return receipt

    def _mutate(self, request_id, kind, payload):
        fingerprint = (kind, canonical(payload))
        active = self._active_operations.get(request_id)
        existing = self._completed_operations.get(request_id)
        if active:
            if active[0] != fingerprint:
                return OperationReceipt(request_id, "REJECTED", error=ApplicationError("REQUEST_ID_REUSED", "Request ID was already used with different inputs."))
            return active[1].receipt
        if existing:
            if existing[0] != fingerprint: return OperationReceipt(request_id, "REJECTED", error=ApplicationError("REQUEST_ID_REUSED", "Request ID was already used with different inputs."))
            return existing[1].receipt
        try:
            if self.service is None or self.project is None or self.principal is None:
                raise LedgerError("PROJECT_REQUIRED", "Taskledger is not initialized for this repository.")
            if kind == "answer_question": result = self.service.answer_question(self.project, self.principal, payload)
            elif kind == "resolve_blocker": result = self.service.resolve_blocker(self.project, self.principal, payload)
            elif kind == "extend_budget":
                from taskledger.controller.journal import Journal
                run_id = payload["run_id"]
                run = Journal(self.service.con,self.service.home).run(run_id)
                if not run or run["project_id"] != self.project["id"]: raise LedgerError("INVALID_REQUEST", "Controller run was not found.")
                grant = Journal(self.service.con,self.service.home).grant_budget(run_id=run_id,kind=payload["kind"],amount=payload["amount"],reason=payload["reason"],principal_id=self.principal["id"])
                result = {"grant_id": grant}
            else: raise LedgerError("INVALID_REQUEST", f"Unknown console operation: {kind}")
            receipt = OperationReceipt(request_id, "APPLIED", run_id=payload.get("run_id"))
            status = OperationStatus(request_id,"SUCCEEDED",receipt,record(**result))
        except (LedgerError, ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, LedgerError):
                code, message = exc.code, exc.message
            else:
                code, message = "INVALID_REQUEST", "Console operation input is invalid."
            receipt = OperationReceipt(request_id,"REJECTED",error=ApplicationError(code,message))
            status = OperationStatus(request_id,"FAILED",receipt,error=receipt.error)
        except Exception:
            receipt = OperationReceipt(request_id, "REJECTED", error=ApplicationError(
                "INTERNAL_ERROR", "Taskledger encountered an unexpected internal error."
            ))
            status = OperationStatus(request_id, "FAILED", receipt, error=receipt.error)
        self._completed_operations[request_id] = (fingerprint,status)
        while len(self._completed_operations) > 256:
            self._completed_operations.popitem(last=False)
        return receipt

    def _admit_owned_operation(self, request_id, fingerprint, receipt, work):
        """Admit and strongly own an async engine job before returning its receipt."""
        active = self._active_operations.get(request_id)
        completed = self._completed_operations.get(request_id)
        existing = active or completed
        if existing:
            if existing[0] != fingerprint:
                return OperationReceipt(request_id, "REJECTED", error=ApplicationError(
                    "REQUEST_ID_REUSED", "Request ID was already used with different inputs."
                ))
            return existing[1].receipt
        if len(self._active_operations) >= 32:
            return OperationReceipt(request_id, "REJECTED", error=ApplicationError(
                "OPERATION_CAPACITY", "Too many unresolved operations are active."
            ))
        accepted = OperationStatus(request_id, "ACCEPTED", receipt)

        async def execute():
            try:
                value = await work()
                status = OperationStatus(request_id, "SUCCEEDED", receipt, record(**value))
            except LedgerError as exc:
                error = ApplicationError(exc.code, exc.message)
                status = OperationStatus(request_id, "FAILED", receipt, error=error)
            except Exception:
                error = ApplicationError("INTERNAL_ERROR", "Taskledger encountered an unexpected internal error.")
                status = OperationStatus(request_id, "FAILED", receipt, error=error)
            self._active_operations.pop(request_id, None)
            self._completed_operations[request_id] = (fingerprint, status)
            while len(self._completed_operations) > 256:
                self._completed_operations.popitem(last=False)
            self._schedule_projection()

        task = asyncio.create_task(execute())
        self._active_operations[request_id] = (fingerprint, accepted, task)
        self._schedule_projection()
        return receipt

    async def operation_status(self, operation_id):
        def lookup():
            active = self._active_operations.get(operation_id)
            completed = self._completed_operations.get(operation_id)
            return (active or completed or (None, OperationStatus(operation_id,"UNKNOWN_OPERATION",None)))[1]
        return await asyncio.wrap_future(self.submit(lookup))

    async def request_pause(self, lifecycle_id: str, reason: str = "USER_INTERRUPTED") -> PauseRequestReceipt:
        with self._active_lock:
            callback = self._active.get(lifecycle_id)
            if callback is not None and lifecycle_id in self._pause_requested:
                return PauseRequestReceipt(lifecycle_id, "ALREADY_REQUESTED")
            if callback is not None: self._pause_requested.add(lifecycle_id)
        if callback is None:
            def validate_target():
                if self.service is None: return
                row = self.service.con.execute("SELECT project_id FROM controller_runs WHERE id=?", (lifecycle_id,)).fetchone()
                if row and row["project_id"] != self.project["id"]:
                    raise LedgerError("INVALID_REQUEST", "Controller run does not belong to this project.")
            await asyncio.wrap_future(self.submit(validate_target))
            return PauseRequestReceipt(lifecycle_id, "NOT_RUNNING")
        callback(reason)
        if self.loop is not None: self.loop.call_soon_threadsafe(self._schedule_projection)
        return PauseRequestReceipt(lifecycle_id, "REQUESTED")

    def _request_active_stops(self) -> None:
        """Set lifecycle latches synchronously; callbacks are thread-safe."""
        with self._active_lock:
            callbacks = tuple(self._active.values())
        for request_stop in callbacks:
            try:
                request_stop("APPLICATION_SHUTDOWN")
            except Exception:
                # Shutdown must continue even if an optional lifecycle owner is
                # already faulted.
                pass

    async def close(self, timeout: float = 10):
        with self._state_lock:
            state = self._state
            if state == "CLOSED":
                return
            self._state = "CLOSING"
        # This is deliberately before scheduling any loop work.  A synchronous
        # check may be occupying the engine loop, but its stop latch is still
        # observable immediately.
        self._request_active_stops()
        if self.feed.reader: self.feed.reader.close()
        if self.loop and self.thread.is_alive():
            async def finish_active():
                tasks = [entry[2] for entry in self._active_operations.values()]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                self.loop.stop()
            if self._shutdown_future is None:
                self._shutdown_future = asyncio.run_coroutine_threadsafe(finish_active(), self.loop)
            try:
                await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(self._shutdown_future)), timeout)
            except asyncio.TimeoutError as exc:
                raise RuntimeError("engine shutdown is waiting for a safe point") from exc
        await asyncio.to_thread(self.thread.join, timeout)
        if self.thread.is_alive():
            raise RuntimeError("engine shutdown is waiting for a safe point")
        self._set_state("CLOSED")
