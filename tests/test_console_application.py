import asyncio
import dataclasses
import json
import sqlite3
import tempfile
import unittest
import sys
from pathlib import Path

from taskledger.application.contracts import SnapshotVersion
from taskledger.application.live import LatestSnapshotFeed
from taskledger.db import transaction
from taskledger.notifications import ChangeNotice, bind_change_sink, unbind_change_sink
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
