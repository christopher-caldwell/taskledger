import asyncio
import dataclasses
import json
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import sys
from pathlib import Path

from taskledger.application.contracts import SnapshotVersion
from taskledger.application.live import LatestSnapshotFeed
from taskledger.db import transaction
from taskledger.notifications import ChangeNotice, bind_change_sink, publish_committed, unbind_change_sink
from taskledger.ui.formatting import safe_text


class Sink:
    def __init__(self): self.notices=[]
    def publish(self,notice): self.notices.append(notice)


class CommittedNotificationTests(unittest.TestCase):
    def setUp(self):
        self.con=sqlite3.connect(":memory:",isolation_level=None);self.con.execute("CREATE TABLE x(v INTEGER)")
        self.sink=Sink();bind_change_sink(self.con,self.sink)
    def tearDown(self): unbind_change_sink(self.con);self.con.close()
    def test_commit_notifies_after_state_is_visible(self):
        with transaction(self.con): self.con.execute("INSERT INTO x VALUES(1)")
        self.assertEqual(len(self.sink.notices),1);self.assertEqual(self.con.execute("SELECT count(*) FROM x").fetchone()[0],1)
    def test_rollback_does_not_notify(self):
        with self.assertRaises(ValueError):
            with transaction(self.con): self.con.execute("INSERT INTO x VALUES(1)");raise ValueError()
        self.assertEqual(self.sink.notices,[])
    def test_observer_failure_does_not_undo_commit(self):
        self.sink.publish=lambda notice:(_ for _ in ()).throw(RuntimeError())
        with transaction(self.con):self.con.execute("INSERT INTO x VALUES(1)")
        self.assertEqual(self.con.execute("SELECT count(*) FROM x").fetchone()[0],1)

    def test_worker_broker_isolated_connection_inherits_owner_observer(self):
        """The broker path has a different SQLite connection but the same sink."""
        from taskledger.controller.worker_broker import WorkerBroker
        class Connection:
            def close(self): pass
        owner_con, isolated_con = Connection(), Connection()
        owner = SimpleNamespace(con=owner_con, home=Path("/tmp/taskledger-test-home"))
        isolated = SimpleNamespace(con=isolated_con, home=owner.home)
        broker = WorkerBroker(owner, object(), Path("/tmp/taskledger-test.sock"))
        observed = Sink(); bind_change_sink(owner_con, observed)
        try:
            # _dispatch models a committed worker tool call made through the
            # very isolated Service created by dispatch().
            with patch("taskledger.db.connect", return_value=isolated_con), \
                 patch("taskledger.service.Service", return_value=isolated), \
                 patch.object(broker, "_dispatch", side_effect=lambda _a, _d, service: (publish_committed(service.con), {"ok": True})[1]):
                self.assertEqual(broker.dispatch("question", {}), {"ok": True})
            self.assertEqual(len(observed.notices), 1)
        finally:
            unbind_change_sink(owner_con)


@dataclasses.dataclass(frozen=True)
class Snapshot:
    version: SnapshotVersion
    value: int


class FeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_seed_and_burst_coalescing(self):
        feed=LatestSnapshotFeed();feed.publish(Snapshot(SnapshotVersion("e",0),0));reader=feed.attach(asyncio.get_running_loop())
        self.assertEqual((await reader.receive()).value,0)
        for i in range(1,10001):feed.publish(Snapshot(SnapshotVersion("e",i),i))
        self.assertEqual(reader.pending.value,10000);self.assertTrue(reader.wake_queued)
        self.assertEqual((await reader.receive()).value,10000);reader.close()
    async def test_cancelled_wait_does_not_close_feed(self):
        feed=LatestSnapshotFeed();feed.publish(Snapshot(SnapshotVersion("e",0),0));reader=feed.attach(asyncio.get_running_loop());await reader.receive()
        wait=asyncio.create_task(reader.receive());await asyncio.sleep(0);wait.cancel()
        with self.assertRaises(asyncio.CancelledError):await wait
        feed.publish(Snapshot(SnapshotVersion("e",1),1));self.assertEqual((await reader.receive()).value,1);reader.close()


class DisplaySafetyTests(unittest.TestCase):
    def test_control_and_markup_are_neutralized(self):
        value=safe_text("[bold]\x1b[31mred\x1b]0;title\x07\x00")
        self.assertNotIn("\x1b",value);self.assertNotIn("\x00",value);self.assertIn("\\[bold]",value)

    def test_public_contract_values_are_json_serializable_and_secret_free(self):
        from taskledger.application.contracts import ApplicationError, OperationReceipt, OperationStatus
        from taskledger.application.queries import record
        values = (
            OperationReceipt("operation", "REJECTED", error=ApplicationError("INVALID_REQUEST", "safe")),
            OperationStatus("operation", "FAILED", None, result=record(
                worker_token="secret", total_tokens=3,
                nested={"authorization": "Bearer hidden", "input_tokens": 2, "safe": "visible"},
            )),
        )
        encoded = json.dumps([dataclasses.asdict(value) for value in values])
        self.assertNotIn("secret", encoded)
        self.assertNotIn("hidden", encoded)
        self.assertIn("visible", encoded)
        self.assertNotIn("worker_token", encoded)


class HostIntegrationTests(unittest.TestCase):
    def test_cli_import_does_not_import_textual(self):
        import taskledger.cli
        self.assertFalse(any(name == "textual" or name.startswith("textual.") for name in sys.modules))

    def test_engine_host_projects_real_ledger_on_owner_thread(self):
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.application.host import EngineHost
        fixture=TaskledgerAcceptance("test_version_flag_returns_installed_version");fixture.setUp()
        try:
            project_id=fixture.init();host=EngineHost(str(fixture.root),project_id=project_id)
            try:
                snapshot=host.current_snapshot();self.assertEqual(snapshot.project.get("id"),project_id)
                owner=host.submit(lambda: __import__("threading").get_ident()).result(2)
                self.assertNotEqual(owner,__import__("threading").get_ident())
            finally:asyncio.run(host.close())
        finally:fixture.tearDown()

    def test_unresolved_operations_are_never_evicted_and_capacity_is_bounded(self):
        from taskledger.application.contracts import OperationReceipt
        from taskledger.application.host import EngineHost
        with tempfile.TemporaryDirectory() as directory:
            import subprocess
            subprocess.run(["git", "init", "-q", directory], check=True)
            host = EngineHost(directory)
            import threading
            release = threading.Event()

            async def scenario():
                async def work():
                    while not release.is_set():
                        await asyncio.sleep(.001)
                    return {"done": True}
                receipts = []
                for index in range(32):
                    operation_id = f"operation-{index}"
                    receipt = OperationReceipt(operation_id, "ACCEPTED")
                    receipts.append(await asyncio.wrap_future(host.submit(
                        host._admit_owned_operation, operation_id, ("test", index), receipt, work
                    )))
                rejected = await asyncio.wrap_future(host.submit(
                    host._admit_owned_operation, "operation-32", ("test", 32),
                    OperationReceipt("operation-32", "ACCEPTED"), work,
                ))
                self.assertTrue(all(item.disposition == "ACCEPTED" for item in receipts))
                duplicate = await asyncio.wrap_future(host.submit(
                    host._admit_owned_operation, "operation-0", ("test", 0),
                    OperationReceipt("operation-0", "ACCEPTED"), work,
                ))
                mismatch = await asyncio.wrap_future(host.submit(
                    host._admit_owned_operation, "operation-0", ("changed", 0),
                    OperationReceipt("operation-0", "ACCEPTED"), work,
                ))
                self.assertEqual(duplicate, receipts[0])
                self.assertEqual(mismatch.error.code, "REQUEST_ID_REUSED")
                self.assertEqual(rejected.error.code, "OPERATION_CAPACITY")
                self.assertEqual((await host.operation_status("operation-0")).phase, "ACCEPTED")
                release.set()
                for _ in range(100):
                    if (await host.operation_status("operation-0")).phase == "SUCCEEDED":
                        break
                    await asyncio.sleep(.01)
                self.assertEqual((await host.operation_status("operation-0")).phase, "SUCCEEDED")
            try:
                asyncio.run(scenario())
            finally:
                asyncio.run(host.close())

    def test_close_requests_stop_before_a_blocked_engine_can_run_cleanup(self):
        from taskledger.application.host import EngineHost
        with tempfile.TemporaryDirectory() as directory:
            import subprocess
            subprocess.run(["git", "init", "-q", directory], check=True)
            host = EngineHost(directory)
            entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
            with host._active_lock:
                host._active["run"] = lambda _reason: stopped.set()
            try:
                host.submit(lambda: (entered.set(), release.wait())).result(.1)
            except TimeoutError:
                pass
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(RuntimeError, "safe point"):
                asyncio.run(host.close(timeout=.01))
            self.assertTrue(stopped.is_set())
            self.assertEqual(host.lifecycle_state, "CLOSING")
            release.set()
            asyncio.run(host.close(timeout=2))
            self.assertEqual(host.lifecycle_state, "CLOSED")
            self.assertFalse(host.thread.is_alive())

    def test_fatal_primary_client_finally_requests_safe_stop_and_closes_host(self):
        """M17: a display-equivalent crash still executes the composition-root cleanup."""
        from taskledger.application.host import EngineHost
        with tempfile.TemporaryDirectory() as directory:
            import subprocess
            subprocess.run(["git", "init", "-q", directory], check=True)
            host = EngineHost(directory)
            stopped = threading.Event()
            with host._active_lock:
                host._active["owned-run"] = lambda _reason: stopped.set()
            async def primary_client():
                try:
                    raise RuntimeError("injected display failure")
                finally:
                    await host.close(timeout=2)
            with self.assertRaisesRegex(RuntimeError, "injected display failure"):
                asyncio.run(primary_client())
            self.assertTrue(stopped.is_set())
            self.assertEqual(host.lifecycle_state, "CLOSED")
            self.assertFalse(host.thread.is_alive())

    def test_fatal_primary_client_closes_owned_lifecycle_and_durable_resources(self):
        """M17: fatal-client cleanup pauses and reconciles an owned active run."""
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.application.contracts import OperationReceipt
        from taskledger.application.host import EngineHost
        from taskledger.application.lifecycle import LifecycleOwner
        from taskledger.controller.fakes import FakeRuntime
        from taskledger.controller.journal import Journal
        from taskledger.controller.model import RuntimeTurnHandle
        from taskledger.db import connect
        from taskledger.service import Service

        fixture = TaskledgerAcceptance("test_version_flag_returns_installed_version")
        fixture.setUp()
        host = None
        try:
            project_id = fixture.init()
            host = EngineHost(str(fixture.root), project_id=project_id)
            lifecycle_entered = threading.Event()

            def admit_owned_lifecycle():
                journal = Journal(host.service.con, host.service.home)
                run_id = journal.create_run(project_id, mode="PROJECT", config={})
                session = journal.create_session(
                    run_id=run_id, project_id=project_id, role="REVIEWER",
                    profile="taskledger_reviewer", subject_id="submission",
                    external_thread_id="review-thread", config_hash="fake", runtime_identity={},
                )
                turn_id = journal.begin_turn(session.id, "reviewer")
                journal.acknowledge_turn(turn_id, RuntimeTurnHandle("review-thread", "review-turn"))
                runtime = FakeRuntime(worker_turns=[], reviewer_turns=[])

                async def hanging_runner(_stop_requested):
                    lifecycle_entered.set()
                    await asyncio.Event().wait()

                owner = LifecycleOwner(
                    host.service, host.project, host.principal, run_id, object(), runtime,
                    runner_factory=hanging_runner,
                )
                receipt = OperationReceipt("owned-lifecycle", "ACCEPTED", run_id=run_id)
                with host._active_lock:
                    host._active[run_id] = owner.request_stop

                async def work():
                    try:
                        return dataclasses.asdict(await owner.run())
                    finally:
                        with host._active_lock:
                            host._active.pop(run_id, None)

                host._admit_owned_operation(
                    "owned-lifecycle", ("test-owned-lifecycle", run_id), receipt, work,
                )
                return run_id, turn_id, runtime, host.service.con

            run_id, turn_id, runtime, owned_connection = host.submit(admit_owned_lifecycle).result(2)
            self.assertTrue(lifecycle_entered.wait(2))

            async def primary_client():
                try:
                    raise RuntimeError("injected primary-client failure")
                finally:
                    await host.close(timeout=2)

            with self.assertRaisesRegex(RuntimeError, "injected primary-client failure"):
                asyncio.run(primary_client())

            self.assertTrue(runtime.closed)
            self.assertEqual(host.lifecycle_state, "CLOSED")
            self.assertFalse(host.thread.is_alive())
            with self.assertRaises(sqlite3.ProgrammingError):
                owned_connection.execute("SELECT 1")

            reopened = Service(connect(fixture.home), fixture.home)
            try:
                journal = Journal(reopened.con, reopened.home)
                self.assertEqual(journal.run(run_id)["state"], "PAUSED")
                turn = reopened.con.execute(
                    "SELECT state,consumed_at FROM controller_turns WHERE id=?", (turn_id,)
                ).fetchone()
                self.assertEqual(turn["state"], "FAILED")
                self.assertIsNotNone(turn["consumed_at"])
                with journal.project_lock(project_id):
                    pass
            finally:
                reopened.con.close()
        finally:
            if host is not None and not host.closed:
                asyncio.run(host.close())
            fixture.tearDown()

    def test_invalid_budget_and_internal_operation_errors_are_safe(self):
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.application.contracts import OperationReceipt
        from taskledger.application.host import EngineHost
        from taskledger.controller.journal import Journal
        fixture = TaskledgerAcceptance("test_version_flag_returns_installed_version"); fixture.setUp()
        try:
            project_id = fixture.init()
            from taskledger.cli import service_for_command
            import argparse, os
            previous = os.getcwd(); os.chdir(fixture.root)
            try: service = service_for_command(argparse.Namespace())
            finally: os.chdir(previous)
            run_id = Journal(service.con, service.home).create_run(project_id, mode="PROJECT", config={})
            service.con.close(); host = EngineHost(str(fixture.root), project_id=project_id)
            async def scenario():
                invalid = await host.extend_budget("bad-budget", run_id=run_id, kind="NOPE", amount=0, reason="bad")
                self.assertEqual(invalid.error.code, "INVALID_REQUEST")
                receipt = OperationReceipt("internal", "ACCEPTED")
                async def broken(): raise RuntimeError("/private/path secret-value")
                await asyncio.wrap_future(host.submit(host._admit_owned_operation, "internal", ("broken", ""), receipt, broken))
                for _ in range(100):
                    status = await host.operation_status("internal")
                    if status.phase != "ACCEPTED": break
                    await asyncio.sleep(.01)
                self.assertEqual(status.error.code, "INTERNAL_ERROR")
                self.assertNotIn("secret-value", status.error.message)
                self.assertNotIn("/private/path", status.error.message)
            try: asyncio.run(scenario())
            finally: asyncio.run(host.close())
        finally: fixture.tearDown()

    def test_real_worker_question_and_blocker_refresh_host_snapshot(self):
        """A real broker tool mutation reaches an attached host feed unaided."""
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.application.host import EngineHost
        from taskledger.controller.worker_broker import WorkerBroker
        from taskledger.db import connect
        from taskledger.service import Service
        fixture = TaskledgerAcceptance("test_version_flag_returns_installed_version"); fixture.setUp()
        host = None
        try:
            project_id = fixture.init()
            _, task_id = fixture.setup_task(project_id)
            fixture.command("plan", "validate", {}, project=project_id)
            _, created = fixture.command("assignment", "create", {"task_id": task_id, "worker_profile": "routine"}, project=project_id)
            token = fixture.assignment_token(created)
            side = Service(connect(fixture.home), fixture.home)
            try:
                worker = side.authenticate(None, token, "WORKER")
            finally:
                side.con.close()
            host = EngineHost(str(fixture.root), project_id=project_id)
            broker = host.submit(lambda: WorkerBroker(host.service, worker, fixture.home / "test-worker.sock")).result(2)
            async def scenario():
                reader = host.open_feed()
                await reader.receive()  # atomic seed; no unrelated host mutation follows.
                result = await asyncio.to_thread(broker.dispatch, "question", {"body": "Need a decision", "blocking": False})
                self.assertIn("question_id", result)
                for _ in range(100):
                    snapshot = await asyncio.wait_for(reader.receive(), 1)
                    if any(item.get("kind") == "QUESTION" for item in snapshot.interventions):
                        break
                else:
                    self.fail("worker-originated question never refreshed the host snapshot")
                result = await asyncio.to_thread(broker.dispatch, "blocker", {
                    "category": "EXTERNAL_DEPENDENCY", "scope_type": "TASK", "description": "Need a dependency"
                })
                self.assertIn("blocker_id", result)
                for _ in range(100):
                    snapshot = await asyncio.wait_for(reader.receive(), 1)
                    if any(item.get("kind") == "BLOCKER" for item in snapshot.interventions):
                        break
                else:
                    self.fail("worker-originated blocker never refreshed the host snapshot")
                reader.close()
            asyncio.run(scenario())
        finally:
            if host is not None: asyncio.run(host.close())
            fixture.tearDown()
    def test_uninitialized_host_does_not_create_ledger(self):
        from taskledger.application.host import EngineHost
        with tempfile.TemporaryDirectory() as directory:
            import subprocess
            subprocess.run(["git","init","-q",directory],check=True)
            host=EngineHost(directory)
            try:self.assertEqual(host.current_snapshot().project.get("effective_phase"),"UNINITIALIZED");self.assertFalse((Path(directory)/".taskledger").exists())
            finally:asyncio.run(host.close())

    def test_existing_open_requires_current_schema_without_migrating(self):
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.db import connect_existing
        fixture=TaskledgerAcceptance("test_version_flag_returns_installed_version");fixture.setUp()
        try:
            fixture.init()
            con = sqlite3.connect(fixture.home / "taskledger.sqlite3")
            before = con.execute("SELECT group_concat(version, ',') FROM schema_migrations ORDER BY version").fetchone()[0]
            con.close()
            opened = connect_existing(fixture.home)
            opened.close()
            con = sqlite3.connect(fixture.home / "taskledger.sqlite3")
            after = con.execute("SELECT group_concat(version, ',') FROM schema_migrations ORDER BY version").fetchone()[0]
            con.execute("DELETE FROM schema_migrations WHERE version=10")
            con.commit(); con.close()
            self.assertEqual(before, after)
            with self.assertRaisesRegex(Exception, "explicit schema upgrade"):
                connect_existing(fixture.home)
        finally: fixture.tearDown()

    def test_history_keeps_independent_source_cursors_and_journal_transitions(self):
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.application.host import EngineHost
        from taskledger.application.contracts import HistoryCursor
        from taskledger.controller.journal import Journal
        fixture=TaskledgerAcceptance("test_version_flag_returns_installed_version");fixture.setUp()
        try:
            project_id=fixture.init()
            from taskledger.cli import service_for_command
            import argparse, os
            prior=os.getcwd();os.chdir(fixture.root)
            try: service=service_for_command(argparse.Namespace())
            finally: os.chdir(prior)
            project, principal=service.auth_orchestrator(project_id,None)
            journal=Journal(service.con,service.home)
            run=journal.create_run(project_id,mode="PREPARATION",config={})
            session=journal.create_session(run_id=run,project_id=project_id,role="TASK_CREATOR",profile="p",subject_id="s",external_thread_id="thread",config_hash="h")
            journal.close_session(session.id)
            service.audit(project_id,principal["id"],"TEST_AUDIT","PROJECT",project_id)
            service.con.close()
            host=EngineHost(str(fixture.root),project_id=project_id)
            async def scenario():
                first=await host.history_page(HistoryCursor(),2)
                second=await host.history_page(first.cursor,20)
                records=(*first.records,*second.records)
                self.assertTrue(any(item.get("source")=="AUDIT" for item in records))
                self.assertTrue(any(item.get("event_type")=="SESSION_CREATED" for item in records))
                self.assertTrue(any(item.get("event_type")=="SESSION_CLOSED" for item in records))
                self.assertGreaterEqual(second.cursor.audit_sequence,first.cursor.audit_sequence)
                self.assertGreaterEqual(second.cursor.controller_sequence,first.cursor.controller_sequence)
            try: asyncio.run(scenario())
            finally: asyncio.run(host.close())
        finally: fixture.tearDown()

    def test_snapshot_and_paged_queries_are_pure_under_write_denial(self):
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.application.queries import ConsoleQueries
        from taskledger.application.contracts import HistoryCursor
        from taskledger.cli import service_for_command
        import argparse, os
        fixture=TaskledgerAcceptance("test_version_flag_returns_installed_version");fixture.setUp()
        try:
            project_id=fixture.init();prior=os.getcwd();os.chdir(fixture.root)
            try: service=service_for_command(argparse.Namespace())
            finally: os.chdir(prior)
            project,_=service.auth_orchestrator(project_id,None)
            denied={sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,sqlite3.SQLITE_CREATE_TABLE,
                sqlite3.SQLITE_DROP_TABLE,sqlite3.SQLITE_ALTER_TABLE}
            service.con.set_authorizer(lambda action,*_: sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK)
            queries=ConsoleQueries(service,project,"pure",lambda _:False)
            self.assertEqual(queries.snapshot().project.get("id"),project_id)
            self.assertEqual(queries.task_page().total,0)
            self.assertIsInstance(queries.history_page(HistoryCursor()).records,tuple)
            service.con.set_authorizer(None);service.con.close()
        finally: fixture.tearDown()

    def test_task_pagination_continues_beyond_compact_thousand(self):
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.application.host import EngineHost
        from taskledger.cli import service_for_command
        from taskledger.core import now
        from taskledger.db import transaction
        import argparse, os
        fixture=TaskledgerAcceptance("test_version_flag_returns_installed_version");fixture.setUp()
        try:
            project_id=fixture.init();prior=os.getcwd();os.chdir(fixture.root)
            try: service=service_for_command(argparse.Namespace())
            finally: os.chdir(prior)
            project,principal=service.auth_orchestrator(project_id,None);stamp=now()
            with transaction(service.con):
                for index in range(1001):
                    task_id=f"bulk-{index:04d}"
                    service.con.execute("INSERT INTO tasks(id,project_id,current_revision,state,created_at,updated_at) VALUES(?,?,1,'PLANNED',?,?)",(task_id,project_id,stamp,stamp))
                    service.con.execute("INSERT INTO task_revisions(task_id,revision,objective,implementation_scope,created_by_principal_id,created_at) VALUES(?,1,?,?,?,?)",(task_id,f"Task {index}","scope",principal["id"],stamp))
            service.con.close();host=EngineHost(str(fixture.root),project_id=project_id)
            async def scenario():
                snapshot=host.current_snapshot()
                self.assertEqual(len(snapshot.tasks),1000)
                self.assertTrue(snapshot.project.get("tasks_truncated"))
                page=await host.task_page(1000,100)
                self.assertEqual((page.total,len(page.records),page.next_offset),(1001,1,None))
            try:asyncio.run(scenario())
            finally:asyncio.run(host.close())
        finally:fixture.tearDown()

    def test_engine_performs_zero_idle_database_queries(self):
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.application.host import EngineHost
        fixture=TaskledgerAcceptance("test_version_flag_returns_installed_version");fixture.setUp()
        try:
            project_id=fixture.init();host=EngineHost(str(fixture.root),project_id=project_id)
            statements=[]
            host.submit(host.service.con.set_trace_callback,statements.append).result(2)
            try: asyncio.run(asyncio.sleep(.15))
            finally: asyncio.run(host.close())
            self.assertEqual(statements,[])
        finally:fixture.tearDown()

    def test_recorded_running_state_is_not_claimed_or_dispatched_on_startup(self):
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.application.host import EngineHost
        from taskledger.cli import service_for_command
        from taskledger.controller.journal import Journal
        import argparse, os
        fixture=TaskledgerAcceptance("test_version_flag_returns_installed_version");fixture.setUp()
        try:
            project_id=fixture.init();prior=os.getcwd();os.chdir(fixture.root)
            try: service=service_for_command(argparse.Namespace())
            finally: os.chdir(prior)
            Journal(service.con,service.home).create_run(project_id,mode="PREPARATION",config={})
            service.con.close()
            host=EngineHost(str(fixture.root),project_id=project_id,
                runtime_factory=lambda **_: self.fail("startup must not create a runtime"))
            try:
                snapshot=host.current_snapshot()
                self.assertEqual(snapshot.run.get("state"),"RUNNING")
                self.assertFalse(snapshot.run.get("owned_by_host"))
            finally:asyncio.run(host.close())
        finally:fixture.tearDown()
