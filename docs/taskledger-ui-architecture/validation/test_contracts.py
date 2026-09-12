"""Standalone architecture contract experiments; NOT Taskledger implementation tests.

Standard library only. The synthetic ledger below deliberately has a tiny schema.
Run: python test_contracts.py
This writes results.json next to the script and makes no network/model calls.
"""
from __future__ import annotations

import asyncio
import dataclasses
import itertools
import json
import platform
import sqlite3
import sys
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable


@dataclasses.dataclass(frozen=True)
class Snapshot:
    epoch: str
    revision: int
    state: str
    tokens: int
    active: tuple[str, ...] = ()


class LatestFeed:
    """One consumer; replaceable full snapshots; at most one queued wake-up.

    The only foreign-thread callback is asyncio's fixed scheduling primitive.
    No display callback runs on the producer thread. No durable events are
    transported through this lossy channel.
    """
    def __init__(self, initial: Snapshot):
        self.lock = threading.Lock()
        self.latest = initial
        self.reader: Reader | None = None
        self.delivery_errors = 0
        self.scheduled_wakes = 0

    def attach(self, loop: asyncio.AbstractEventLoop) -> Reader:
        with self.lock:
            if self.reader is not None:
                raise RuntimeError("one display subscription only")
            reader = Reader(self, loop)
            self.reader = reader
            reader.pending = self.latest
            self._wake_locked(reader)
            return reader

    def publish(self, snapshot: Snapshot) -> bool:
        with self.lock:
            if snapshot.epoch != self.latest.epoch:
                return False
            if snapshot.revision <= self.latest.revision:
                return False
            self.latest = snapshot
            if self.reader is not None:
                self.reader.pending = snapshot
                self._wake_locked(self.reader)
            return True

    def _wake_locked(self, reader: Reader) -> None:
        if reader.wake_queued:
            return
        reader.wake_queued = True
        try:
            reader.loop.call_soon_threadsafe(reader._wake)
            self.scheduled_wakes += 1
        except RuntimeError:  # Closed consumer loop; state publication still wins.
            reader.wake_queued = False
            reader.closed = True
            self.reader = None
            self.delivery_errors += 1


class Reader:
    def __init__(self, feed: LatestFeed, loop: asyncio.AbstractEventLoop):
        self.feed = feed
        self.loop = loop
        self.available = asyncio.Event()
        self.pending: Snapshot | None = None
        self.closed = False
        self.wake_queued = False

    def _wake(self) -> None:
        with self.feed.lock:
            self.wake_queued = False
        self.available.set()

    async def receive(self) -> Snapshot:
        while True:
            with self.feed.lock:
                if self.closed:
                    raise StopAsyncIteration
                if self.pending is not None:
                    value = self.pending
                    self.pending = None
                    return value
                # Clearing happens on the consumer loop while holding the same
                # lock used by publish. A subsequent publish schedules a wake.
                self.available.clear()
            await self.available.wait()

    def close(self) -> None:
        # Called on the consumer loop, not on the producer thread.
        with self.feed.lock:
            self.closed = True
            if self.feed.reader is self:
                self.feed.reader = None
        self.available.set()


class SyntheticLedger:
    """Real SQLite transactions, synthetic schema and application methods."""
    def __init__(self):
        self.con = sqlite3.connect(":memory:", isolation_level=None)
        self.con.executescript("""
            CREATE TABLE state(id INTEGER PRIMARY KEY, value INTEGER NOT NULL);
            INSERT INTO state VALUES(1,0);
            CREATE TABLE history(sequence INTEGER PRIMARY KEY AUTOINCREMENT, value INTEGER NOT NULL);
        """)
        self.revision = 0
        self.observation_errors = 0
        self.hook: Callable[[Snapshot], Any] = lambda _: None

    def value(self) -> int:
        return self.con.execute("SELECT value FROM state WHERE id=1").fetchone()[0]

    def change(self, value: int, *, rollback: bool = False) -> bool:
        self.con.execute("BEGIN IMMEDIATE")
        try:
            self.con.execute("UPDATE state SET value=? WHERE id=1", (value,))
            self.con.execute("INSERT INTO history(value) VALUES(?)", (value,))
            if rollback:
                raise ValueError("injected transaction failure")
        except Exception:
            self.con.rollback()
            return False
        else:
            self.con.commit()
        self.revision += 1
        try:
            # Deliberately outside the transaction; publication cannot roll it back.
            self.hook(Snapshot("engine", self.revision, "RUNNING", self.value()))
        except Exception:
            self.observation_errors += 1
        return True

    def snapshot(self) -> Snapshot:
        if self.con.in_transaction:
            raise RuntimeError("never project an open write transaction")
        return Snapshot("engine", self.revision, "RUNNING", self.value())

    def close(self) -> None:
        self.con.close()


class EngineLoop:
    """Minimal independent loop owner for concurrency experiments."""
    def __init__(self):
        self.ready = threading.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread = threading.Thread(target=self._run, daemon=False)
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError("engine test loop did not start")

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        try:
            self.loop.run_forever()
        finally:
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()

    def submit(self, coro: Any) -> Future:
        assert self.loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def close(self) -> None:
        assert self.loop is not None
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        if self.thread.is_alive():
            raise RuntimeError("engine test loop did not stop")


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.ledger = SyntheticLedger()

    def tearDown(self):
        self.ledger.close()

    def test_P01_commit_is_visible_before_notification(self):
        seen = []
        self.ledger.hook = lambda snapshot: seen.append((self.ledger.con.in_transaction, self.ledger.value(), snapshot.tokens))
        self.assertTrue(self.ledger.change(7))
        self.assertEqual(seen, [(False, 7, 7)])

    def test_P02_rollback_emits_no_committed_snapshot(self):
        seen = []
        self.ledger.hook = seen.append
        self.assertFalse(self.ledger.change(7, rollback=True))
        self.assertEqual((seen, self.ledger.value()), ([], 0))
        self.assertEqual(self.ledger.con.execute("SELECT COUNT(*) FROM history").fetchone()[0], 0)

    def test_P03_notification_failure_does_not_undo_commit(self):
        def broken(_: Snapshot):
            raise RuntimeError("observer unavailable")
        self.ledger.hook = broken
        self.assertTrue(self.ledger.change(8))
        self.assertEqual(self.ledger.value(), 8)
        self.assertEqual(self.ledger.observation_errors, 1)

    def test_P04_resnapshot_recovers_lost_publication(self):
        self.ledger.change(13)
        self.assertEqual(self.ledger.snapshot().tokens, 13)

    def test_P05_snapshot_rejects_open_write_transaction(self):
        self.ledger.con.execute("BEGIN IMMEDIATE")
        self.ledger.con.execute("UPDATE state SET value=99")
        with self.assertRaises(RuntimeError):
            self.ledger.snapshot()
        self.ledger.con.rollback()

    def test_P06_query_survives_write_denial(self):
        denied = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                  sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE}
        def authorizer(action, arg1, arg2, db, trigger):
            return sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK
        self.ledger.con.set_authorizer(authorizer)
        self.assertEqual(self.ledger.snapshot().tokens, 0)
        with self.assertRaises(sqlite3.DatabaseError):
            self.ledger.con.execute("UPDATE state SET value=9")
        self.ledger.con.set_authorizer(None)

    def test_P07_sqlite_connection_cannot_cross_owner_thread(self):
        errors = []
        def foreign():
            try:
                self.ledger.value()
            except Exception as exc:
                errors.append(type(exc))
        t = threading.Thread(target=foreign)
        t.start(); t.join()
        self.assertEqual(errors, [sqlite3.ProgrammingError])

    def test_P08_coalescing_does_not_delete_durable_history(self):
        feed = LatestFeed(self.ledger.snapshot())
        self.ledger.hook = feed.publish
        for i in range(1, 101):
            self.ledger.change(i)
        self.assertEqual(feed.latest.tokens, 100)
        self.assertEqual(self.ledger.con.execute("SELECT COUNT(*) FROM history").fetchone()[0], 100)

    def test_P09_full_snapshot_replacement_does_not_double_count_usage(self):
        feed = LatestFeed(Snapshot("engine", 0, "RUNNING", 0))
        feed.publish(Snapshot("engine", 1, "RUNNING", 100))
        feed.publish(Snapshot("engine", 2, "RUNNING", 100))
        self.assertEqual(feed.latest.tokens, 100)

    def test_P10_deeply_immutable_contract_example(self):
        s = Snapshot("e", 1, "RUNNING", 10, ("a",))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            s.tokens = 5
        with self.assertRaises(TypeError):
            s.active[0] = "b"
        self.assertEqual(json.loads(json.dumps(dataclasses.asdict(s)))["active"], ["a"])

    def test_P11_stale_revision_and_wrong_epoch_are_rejected(self):
        feed = LatestFeed(Snapshot("e", 10, "PAUSED", 40))
        self.assertFalse(feed.publish(Snapshot("e", 9, "RUNNING", 30)))
        self.assertFalse(feed.publish(Snapshot("previous-process", 100, "RUNNING", 60)))
        self.assertEqual(feed.latest.state, "PAUSED")

    def test_P12_naive_snapshot_then_subscribe_has_a_counterexample(self):
        committed = 0
        displayed = committed  # read initial snapshot
        committed = 1          # change before listener registration
        listeners = ["display"]  # too late; there are no further changes
        self.assertTrue(listeners)
        self.assertNotEqual(displayed, committed)

    def test_P13_atomic_attach_publish_permutations_converge(self):
        # Abstract atomic operations; covers both linearized orderings.
        for ordering in itertools.permutations(("attach", "publish")):
            latest = 0; attached = False; pending = None
            for action in ordering:
                if action == "publish":
                    latest = 1
                    if attached: pending = latest
                else:
                    attached = True; pending = latest
            self.assertEqual(pending, latest)

    def test_P14_no_observer_does_not_change_synthetic_workload(self):
        results = []
        for observe in (False, True):
            ledger = SyntheticLedger()
            try:
                feed = LatestFeed(ledger.snapshot())
                if observe: ledger.hook = feed.publish
                for i in range(1, 101): ledger.change(i)
                results.append((ledger.value(), ledger.con.execute("SELECT COUNT(*) FROM history").fetchone()[0]))
            finally:
                ledger.close()
        self.assertEqual(results[0], results[1])


class FeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_P15_attach_seeds_current_snapshot_without_waiting_for_change(self):
        feed = LatestFeed(Snapshot("e", 5, "PAUSED", 10))
        reader = feed.attach(asyncio.get_running_loop())
        self.assertEqual((await reader.receive()).revision, 5)
        reader.close()

    async def test_P16_burst_has_one_slot_and_one_queued_wake(self):
        feed = LatestFeed(Snapshot("e", 0, "RUNNING", 0))
        reader = feed.attach(asyncio.get_running_loop())
        # Do not yield: consumer loop is deliberately not draining callbacks.
        for i in range(1, 10001):
            feed.publish(Snapshot("e", i, "RUNNING", i))
        self.assertEqual(feed.scheduled_wakes, 1)
        self.assertEqual(reader.pending.revision, 10000)
        self.assertEqual((await reader.receive()).tokens, 10000)
        reader.close()

    async def test_P17_publish_wakes_waiting_consumer_across_threads(self):
        feed = LatestFeed(Snapshot("e", 0, "RUNNING", 0))
        reader = feed.attach(asyncio.get_running_loop())
        await reader.receive()
        waiter = asyncio.create_task(reader.receive())
        await asyncio.sleep(0)
        t = threading.Thread(target=lambda: feed.publish(Snapshot("e", 1, "RUNNING", 22)))
        t.start()
        value = await asyncio.wait_for(waiter, 2)
        t.join()
        self.assertEqual(value.tokens, 22)
        reader.close()

    async def test_P18_cancelled_display_wait_does_not_cancel_producer(self):
        feed = LatestFeed(Snapshot("e", 0, "RUNNING", 0))
        reader = feed.attach(asyncio.get_running_loop())
        await reader.receive()
        waiter = asyncio.create_task(reader.receive())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError): await waiter
        feed.publish(Snapshot("e", 1, "COMPLETED", 50))
        self.assertEqual((await reader.receive()).state, "COMPLETED")
        reader.close()

    async def test_P19_unsubscribe_is_local_and_reattach_reads_latest(self):
        feed = LatestFeed(Snapshot("e", 0, "RUNNING", 0))
        old = feed.attach(asyncio.get_running_loop()); old.close()
        feed.publish(Snapshot("e", 1, "PAUSED", 51))
        new = feed.attach(asyncio.get_running_loop())
        self.assertEqual((await new.receive()).tokens, 51)
        with self.assertRaises(StopAsyncIteration): await old.receive()
        new.close()

    async def test_P20_second_subscriber_is_rejected(self):
        feed = LatestFeed(Snapshot("e", 0, "RUNNING", 0))
        reader = feed.attach(asyncio.get_running_loop())
        with self.assertRaises(RuntimeError): feed.attach(asyncio.get_running_loop())
        reader.close()

    async def test_P21_dead_display_loop_does_not_stop_publication(self):
        loop = asyncio.new_event_loop(); loop.close()
        feed = LatestFeed(Snapshot("e", 0, "RUNNING", 0))
        feed.attach(loop)
        self.assertTrue(feed.publish(Snapshot("e", 1, "COMPLETED", 9)))
        self.assertEqual(feed.latest.tokens, 9)
        self.assertEqual(feed.delivery_errors, 1)

    async def test_P22_slow_consumer_converges_after_repeated_bursts(self):
        feed = LatestFeed(Snapshot("e", 0, "RUNNING", 0))
        reader = feed.attach(asyncio.get_running_loop())
        last = -1
        for block in range(20):
            for j in range(1, 51):
                value = block * 50 + j
                feed.publish(Snapshot("e", value, "RUNNING", value))
            await asyncio.sleep(0)
            snapshot = await asyncio.wait_for(reader.receive(), 2)
            self.assertGreater(snapshot.revision, last)
            last = snapshot.revision
        self.assertEqual(last, 1000)
        reader.close()


class LoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_P23_ui_loop_advances_while_engine_thread_is_blocked(self):
        engine = EngineLoop()
        entered = threading.Event(); release = threading.Event()
        async def blocking_engine_work():
            entered.set()
            if not release.wait(5): raise RuntimeError("test release timed out")
            return 42
        future = engine.submit(blocking_engine_work())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            ticks = 0
            for _ in range(25):
                await asyncio.sleep(0)
                ticks += 1
            self.assertEqual(ticks, 25)
            self.assertFalse(future.done())
        finally:
            release.set()
            self.assertEqual(await asyncio.wrap_future(future), 42)
            engine.close()

    async def test_P24_cancelled_receipt_wait_does_not_cancel_owned_job(self):
        engine = EngineLoop()
        entered = threading.Event(); release = threading.Event()
        async def owned_job():
            entered.set()
            while not release.is_set(): await asyncio.sleep(0.001)
            return "COMPLETED"
        future = engine.submit(owned_job())
        async def receipt_wait():
            return await asyncio.shield(asyncio.wrap_future(future))
        waiter = asyncio.create_task(receipt_wait())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError): await waiter
            self.assertFalse(future.cancelled())
            release.set()
            self.assertEqual(await asyncio.wrap_future(future), "COMPLETED")
        finally:
            release.set()
            engine.close()

    async def test_P25_pause_latch_is_not_paused_and_blocks_next_admission(self):
        requested = threading.Event()
        state = "RUNNING"
        requested.set()  # Thread-safe intent, not a durable completion claim.
        self.assertEqual(state, "RUNNING")
        dispatches = []
        if not requested.is_set(): dispatches.append("new-model-turn")
        self.assertEqual(dispatches, [])
        # A fake reconciliation step is explicit; this is not a Codex test.
        state = "PAUSED"
        self.assertEqual(state, "PAUSED")

    async def test_P26_database_is_created_and_used_in_engine_thread(self):
        engine = EngineLoop()
        async def work():
            ledger = SyntheticLedger()
            try:
                ledger.change(77)
                return ledger.value(), threading.get_ident()
            finally: ledger.close()
        try:
            value, owner = await asyncio.wrap_future(engine.submit(work()))
            self.assertEqual(value, 77)
            self.assertNotEqual(owner, threading.get_ident())
        finally: engine.close()


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []
    def addSuccess(self, test):
        super().addSuccess(test)
        self.cases.append({"test": test.id(), "status": "PASS"})
    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.cases.append({"test": test.id(), "status": "FAIL"})
    def addError(self, test, err):
        super().addError(test, err)
        self.cases.append({"test": test.id(), "status": "ERROR"})


if __name__ == "__main__":
    started = time.perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(suite)
    report = {
        "scope": "Standalone synthetic contract experiments, not Taskledger or Textual integration tests",
        "python": sys.version,
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "duration_seconds": round(time.perf_counter() - started, 6),
        "model_calls": 0,
        "taskledger_repository_tests_run": False,
        "textual_tests_run": False,
        "cases": sorted(result.cases, key=lambda item: item["test"]),
    }
    Path(__file__).with_name("results.json").write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(0 if result.wasSuccessful() else 1)
