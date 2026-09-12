import asyncio
import dataclasses
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

    def test_uninitialized_host_does_not_create_ledger(self):
        from taskledger.application.host import EngineHost
        with tempfile.TemporaryDirectory() as directory:
            import subprocess
            subprocess.run(["git","init","-q",directory],check=True)
            host=EngineHost(directory)
            try:self.assertEqual(host.current_snapshot().project.get("effective_phase"),"UNINITIALIZED");self.assertFalse((Path(directory)/".taskledger").exists())
            finally:asyncio.run(host.close())
