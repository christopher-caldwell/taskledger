import asyncio
import unittest

try:
    import textual
except ImportError:
    textual = None

from taskledger.application.contracts import ConsoleSnapshot, Record, SnapshotVersion


def rec(**values): return Record(tuple(sorted(values.items())))


class FakeReader:
    def __init__(self,snapshot): self.values=asyncio.Queue();self.values.put_nowait(snapshot);self.closed=False
    async def receive(self):
        if self.closed:raise StopAsyncIteration
        return await self.values.get()
    def close(self):self.closed=True


class FakeClient:
    def __init__(self,snapshot):self.reader=FakeReader(snapshot);self.attaches=0;self.pauses=[];self.closed=False
    def open_feed(self):self.attaches+=1;return self.reader
    async def request_pause(self,value):self.pauses.append(value)
    async def close(self):self.closed=True


@unittest.skipUnless(textual,"install taskledger[tui] to run Textual tests")
class ConsoleUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_snapshot_and_single_subscription(self):
        from taskledger.ui.app import TaskledgerApp
        snapshot=ConsoleSnapshot(1,SnapshotVersion("e",1),"now",rec(id="p",repository_root="/repo",effective_phase="EXECUTING"),None,rec(id="r",state="RUNNING",owned_by_host=False),(rec(id="t",state="PLANNED",objective="work"),),(),rec(total_tokens=None),(),())
        client=FakeClient(snapshot);app=TaskledgerApp(client)
        async with app.run_test(size=(120,36)) as pilot:
            await pilot.pause();self.assertEqual(client.attaches,1)
            self.assertIn("work",str(app.query_one("#tasks-body").render()))
            await pilot.press("f2");self.assertEqual(app.query_one("#sections").active,"tasks")
        self.assertEqual(client.attaches,1)

    async def test_stale_snapshot_is_rejected(self):
        from taskledger.ui.app import TaskledgerApp
        newer=ConsoleSnapshot(1,SnapshotVersion("e",2),"now",rec(id="p",repository_root="/new"),None,None,(),(),rec(),(),())
        older=ConsoleSnapshot(1,SnapshotVersion("e",1),"now",rec(id="p",repository_root="/old"),None,None,(),(),rec(),(),())
        client=FakeClient(newer);app=TaskledgerApp(client)
        async with app.run_test() as pilot:
            await pilot.pause();app._render(older)
            self.assertEqual(app.snapshot.version.revision,2)
