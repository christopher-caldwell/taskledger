import asyncio
import unittest
from pathlib import Path

try:
    import textual
except ImportError:
    textual = None

from taskledger.application.contracts import ConsoleSnapshot, PauseRequestReceipt, Record, SnapshotVersion


def rec(**values): return Record(tuple(sorted(values.items())))


class FakeReader:
    def __init__(self,snapshot): self.values=asyncio.Queue();self.values.put_nowait(snapshot);self.closed=False
    async def receive(self):
        if self.closed:raise StopAsyncIteration
        value=await self.values.get()
        if isinstance(value,BaseException):raise value
        return value
    def close(self):self.closed=True


class FakeClient:
    def __init__(self,snapshot):
        self.reader=FakeReader(snapshot);self.attaches=0;self.pauses=[];self.closed=False;self.refreshes=0
        self.refresh_value=None;self.refresh_error=None
    def open_feed(self):self.attaches+=1;return self.reader
    async def request_pause(self,value):self.pauses.append(value);return PauseRequestReceipt(value,"REQUESTED")
    async def refresh_snapshot(self):
        self.refreshes+=1
        if self.refresh_error:raise self.refresh_error
        return self.refresh_value
    async def close(self):self.closed=True


def snapshot(
    revision=1, *, epoch="e", tasks=(), sessions=(), activity=(), interventions=(), owned=False,
    task_count=8, tasks_truncated=False, usage=None,
):
    progress=(
        ("requirements", (("total", 9), ("complete", 7), ("blocked", 1), ("awaiting_final_verification", 1))),
        ("tasks", (("completed", 5), ("active_implementation", 1), ("awaiting_submission_verification", 1))),
    )
    return ConsoleSnapshot(
        1, SnapshotVersion(epoch,revision), "now",
        rec(id="p",repository_root="/repo",canonical_branch="main",effective_phase="EXECUTING",progress=progress,task_count=task_count,tasks_truncated=tasks_truncated,cache_health="HEALTHY"),
        None, rec(id="r",state="RUNNING",owned_by_host=owned,pause_requested=False), tasks, sessions,
        usage or rec(
            known_token_subtotal=31240,accounting_quality="INCOMPLETE",completed_turns=2,
            unresolved_turn_count=3,input_tokens=100,cached_input_tokens=75,
        ),
        interventions, activity,
    )


class UiBoundaryTests(unittest.TestCase):
    def test_ui_does_not_import_mechanical_layers(self):
        ui_root=Path(__file__).parents[1]/"src"/"taskledger"/"ui"
        source="\n".join(path.read_text() for path in ui_root.rglob("*.py"))
        for forbidden in ("import sqlite3","taskledger.service","controller.journal","controller.project","controller.supervisor","controller.app_server","taskledger.git"):
            self.assertNotIn(forbidden,source)


@unittest.skipUnless(textual,"install taskledger[tui] to run Textual tests")
class ConsoleUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_snapshot_and_single_subscription(self):
        from taskledger.ui.app import TaskledgerApp
        value=snapshot(tasks=(rec(id="t",state="PLANNED",objective="work",dependency_count=0,blocker_count=0),))
        client=FakeClient(value);app=TaskledgerApp(client)
        async with app.run_test(size=(120,36)) as pilot:
            await pilot.pause();self.assertEqual(client.attaches,1)
            table=app.query_one("#tasks-table")
            self.assertEqual(str(table.get_cell("t","objective")),"work")
            for key,tab in (("f2","tasks"),("f3","agents"),("f4","activity"),("f5","usage"),("f6","interventions"),("f1","overview")):
                await pilot.press(key);self.assertEqual(app.query_one("#sections").active,tab)
        self.assertEqual(client.attaches,1)

    async def test_stale_snapshot_is_rejected(self):
        from taskledger.ui.app import TaskledgerApp
        newer=snapshot(12,tasks=(rec(id="new",state="PLANNED",objective="new",dependency_count=0,blocker_count=0),))
        older=snapshot(11,tasks=(rec(id="old",state="PLANNED",objective="old",dependency_count=0,blocker_count=0),))
        client=FakeClient(newer);app=TaskledgerApp(client)
        async with app.run_test() as pilot:
            await pilot.pause();app._render(older)
            self.assertEqual(app.snapshot.version.revision,12)
            self.assertEqual(str(app.query_one("#tasks-table").get_cell("new","objective")),"new")
            fresh_epoch=snapshot(1,epoch="next")
            self.assertTrue(app._accept_snapshot(fresh_epoch))
            self.assertEqual(app.snapshot.version.engine_epoch,"next")

    async def test_overview_uses_authoritative_requirement_task_and_usage_values(self):
        from taskledger.ui.app import TaskledgerApp
        intervention=rec(id="q",kind="QUESTION",scope_id="t",description="Choose strategy")
        client=FakeClient(snapshot(interventions=(intervention,)));app=TaskledgerApp(client)
        async with app.run_test(size=(120,36)) as pilot:
            await pilot.pause()
            rendered=str(app.query_one("#overview-body").render())
            self.assertIn("7 / 9 complete",rendered)
            self.assertIn("5 / 8 completed",rendered)
            self.assertIn("31.2k known tokens",rendered)
            self.assertIn("QUESTION",rendered)

    async def test_task_selection_survives_keyed_incremental_update(self):
        from taskledger.ui.app import TaskledgerApp
        first=snapshot(1,tasks=(
            rec(id="a",state="PLANNED",objective="A",dependency_count=0,blocker_count=0),
            rec(id="b",state="ASSIGNED",objective="B",dependency_count=0,blocker_count=0),
        ))
        client=FakeClient(first);app=TaskledgerApp(client)
        async with app.run_test(size=(120,36)) as pilot:
            await pilot.pause();table=app.query_one("#tasks-table")
            table.move_cursor(row=table.get_row_index("b"));await pilot.pause()
            self.assertEqual(app.selected_task_id,"b")
            client.reader.values.put_nowait(snapshot(2,tasks=(
                rec(id="b",state="SUBMITTED",objective="B updated",dependency_count=0,blocker_count=0),
                rec(id="a",state="COMPLETED",objective="A",dependency_count=0,blocker_count=0),
            )))
            await pilot.pause()
            self.assertEqual(table.selected_key(),"b")
            self.assertEqual(str(table.get_cell("b","objective")),"B updated")

    async def test_usage_uses_production_fields_and_derives_non_cached_input(self):
        from taskledger.ui.app import TaskledgerApp
        client=FakeClient(snapshot());app=TaskledgerApp(client)
        async with app.run_test(size=(120,36)) as pilot:
            await pilot.pause()
            rendered=str(app.query_one("#usage-body").render())
            self.assertIn("Unresolved turns  3",rendered)
            self.assertIn("Non-cached input  25",rendered)
            client.reader.values.put_nowait(snapshot(
                2,usage=rec(
                    known_token_subtotal=12,accounting_quality="INCOMPLETE",unresolved_turn_count=0,
                    input_tokens=100,
                ),
            ))
            await pilot.pause()
            rendered=str(app.query_one("#usage-body").render())
            self.assertIn("Non-cached input  —",rendered)
            client.reader.values.put_nowait(snapshot(
                3,usage=rec(
                    known_token_subtotal=12,accounting_quality="INCOMPLETE",unresolved_turn_count=0,
                    cached_input_tokens=75,
                ),
            ))
            await pilot.pause()
            rendered=str(app.query_one("#usage-body").render())
            self.assertIn("Non-cached input  —",rendered)

    async def test_refresh_accepts_snapshot_after_observer_failure(self):
        from taskledger.ui.app import TaskledgerApp
        first=snapshot(1,tasks=(rec(id="t",state="PLANNED",objective="old",dependency_count=0,blocker_count=0),))
        second=snapshot(2,tasks=(rec(id="t",state="PLANNED",objective="current",dependency_count=0,blocker_count=0),))
        client=FakeClient(first);app=TaskledgerApp(client)
        async with app.run_test(size=(120,36)) as pilot:
            await pilot.pause()
            client.reader.values.put_nowait(RuntimeError("feed unavailable"));await pilot.pause()
            self.assertTrue(app._observer.done())
            self.assertIn("OBSERVATION DEGRADED",str(app.query_one("#global-banner").render()))
            client.refresh_value=second
            await pilot.press("r");await pilot.pause()
            self.assertEqual(app.snapshot.version.revision,2)
            self.assertEqual(str(app.query_one("#tasks-table").get_cell("t","objective")),"current")
            self.assertIsNone(app.observation_error)
            client.refresh_error=RuntimeError("refresh failed")
            await pilot.press("r");await pilot.pause()
            self.assertIn("refresh failed",str(app.query_one("#global-banner").render()))

    async def test_agents_follow_semantic_order(self):
        from taskledger.ui.app import TaskledgerApp
        first=snapshot(1,sessions=(
            rec(id="a",run_id="r",state="ACTIVE",latest_turn_state="COMPLETED",created_at="1"),
            rec(id="b",run_id="r",state="ACTIVE",latest_turn_state="RUNNING",created_at="2"),
        ))
        client=FakeClient(first);app=TaskledgerApp(client)
        async with app.run_test(size=(120,36)) as pilot:
            await pilot.pause();table=app.query_one("#agents-table")
            self.assertEqual([str(row.key.value) for row in table.ordered_rows],["b","a"])
            client.reader.values.put_nowait(snapshot(2,sessions=(
                rec(id="a",run_id="r",state="ACTIVE",latest_turn_state="RUNNING",created_at="1"),
                rec(id="b",run_id="r",state="CLOSED",latest_turn_state="COMPLETED",created_at="2"),
            )))
            await pilot.pause()
            self.assertEqual([str(row.key.value) for row in table.ordered_rows],["a","b"])

    async def test_activity_follows_supplied_order_after_deduplication(self):
        from taskledger.ui.app import TaskledgerApp
        event_a=rec(source="audit",sequence=1,event_type="A")
        event_b=rec(source="audit",sequence=2,event_type="B")
        event_c=rec(source="audit",sequence=3,event_type="C")
        client=FakeClient(snapshot(1,activity=(event_b,event_a)));app=TaskledgerApp(client)
        async with app.run_test(size=(120,36)) as pilot:
            await pilot.pause();table=app.query_one("#activity-table")
            self.assertEqual([str(row.key.value) for row in table.ordered_rows],["audit:2","audit:1"])
            client.reader.values.put_nowait(snapshot(2,activity=(event_c,event_b,event_b,event_a)))
            await pilot.pause()
            self.assertEqual([str(row.key.value) for row in table.ordered_rows],["audit:3","audit:2","audit:1"])

    async def test_task_note_updates_when_only_count_metadata_changes(self):
        from taskledger.ui.app import TaskledgerApp
        tasks=(
            rec(id="a",state="PLANNED",objective="A",dependency_count=0,blocker_count=0),
            rec(id="b",state="PLANNED",objective="B",dependency_count=0,blocker_count=0),
        )
        client=FakeClient(snapshot(1,tasks=tasks,task_count=2,tasks_truncated=False));app=TaskledgerApp(client)
        async with app.run_test(size=(120,36)) as pilot:
            await pilot.pause()
            self.assertEqual(str(app.query_one("#tasks-note").render()),"")
            client.reader.values.put_nowait(snapshot(2,tasks=tasks,task_count=3,tasks_truncated=True))
            await pilot.pause()
            self.assertIn("Showing 2 of 3 tasks",str(app.query_one("#tasks-note").render()))

    async def test_pause_refresh_and_responsive_modes(self):
        from taskledger.ui.app import TaskledgerApp
        client=FakeClient(snapshot(owned=True));app=TaskledgerApp(client)
        async with app.run_test(size=(100,30)) as pilot:
            await pilot.pause();self.assertEqual(app.layout_mode,"standard")
            await pilot.press("r");await pilot.pause();self.assertEqual(client.refreshes,1)
            await pilot.press("ctrl+p");await pilot.pause();self.assertEqual(client.pauses,["r"])
            self.assertIn("PAUSE REQUESTED",str(app.query_one("#global-banner").render()))
            await pilot.resize_terminal(55,18);await pilot.pause();self.assertEqual(app.layout_mode,"below-minimum")
            await pilot.resize_terminal(140,40);await pilot.pause();self.assertEqual(app.layout_mode,"wide")
