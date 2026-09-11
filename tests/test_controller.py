from __future__ import annotations

import tempfile
import unittest
import asyncio
from pathlib import Path

from taskledger.controller.fakes import FakeLedger, FakeRuntime, TurnScript, accepted_verdict, rejected_verdict
from taskledger.controller.journal import Journal
from taskledger.controller.model import PauseReason, SupervisorStatus, Usage
from taskledger.controller.supervisor import Supervisor, SupervisorConfig, parse_verdict
from taskledger.controller.worker_broker import WorkerBroker, broker_socket_path, request
from taskledger.core import now
from taskledger.db import connect, transaction


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.con = connect(self.home)
        stamp = now()
        with transaction(self.con):
            self.con.execute(
                "INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("p1", str(self.home), str(self.home / ".git"), "main", stamp, "ACTIVE", stamp, None, None, stamp, stamp),
            )
        self.journal = Journal(self.con, self.home)

    def tearDown(self):
        self.con.close()
        self.temp.cleanup()

    def supervisor(self, ledger, runtime, **limits):
        config = SupervisorConfig(**limits)
        run_id = self.journal.create_run("p1", mode="ASSIGNMENT", config=config.__dict__)
        return Supervisor(project_id="p1", run_id=run_id, ledger=ledger, runtime=runtime, journal=self.journal, config=config)

    async def test_normal_turn_completion_continues_until_submission(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(
            worker_turns=[TurnScript(effect=ledger.progress), TurnScript(effect=ledger.submit)],
            reviewer_turns=[TurnScript(structured_output=accepted_verdict())],
        )
        result = await self.supervisor(ledger, runtime).run_assignment("a1")
        self.assertEqual(result.status, SupervisorStatus.INTEGRATED)
        self.assertEqual(result.worker_turns, 2)
        self.assertEqual(runtime.sessions_started, 2)

    async def test_rejection_reuses_worker_session_then_reviews_new_submission(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(
            worker_turns=[TurnScript(effect=ledger.submit), TurnScript(effect=ledger.submit)],
            reviewer_turns=[TurnScript(structured_output=rejected_verdict()), TurnScript(structured_output=accepted_verdict())],
        )
        result = await self.supervisor(ledger, runtime).run_assignment("a1")
        self.assertEqual(result.status, SupervisorStatus.INTEGRATED)
        self.assertEqual(result.submission_reviews, 2)
        worker_sessions = self.con.execute("SELECT COUNT(*) FROM controller_sessions WHERE role='WORKER'").fetchone()[0]
        self.assertEqual(worker_sessions, 1)

    async def test_stall_budget_survives_turns(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(worker_turns=[TurnScript(), TurnScript()], reviewer_turns=[])
        result = await self.supervisor(ledger, runtime, max_consecutive_stalled_turns=2).run_assignment("a1")
        self.assertEqual(result.pause_reason, PauseReason.STALLED)
        self.assertEqual(result.worker_turns, 2)

    async def test_worker_turn_budget_stops_before_extra_dispatch(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(worker_turns=[TurnScript(effect=ledger.progress)], reviewer_turns=[])
        result = await self.supervisor(ledger, runtime, max_worker_turns=1).run_assignment("a1")
        self.assertEqual(result.pause_reason, PauseReason.MAX_TURNS)
        self.assertEqual(runtime.turns_started, 1)

    async def test_token_budget_is_an_admission_limit(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(worker_turns=[TurnScript(effect=ledger.progress, usage=Usage(input_tokens=11))], reviewer_turns=[])
        result = await self.supervisor(ledger, runtime, max_total_tokens=10).run_assignment("a1")
        self.assertEqual(result.pause_reason, PauseReason.BUDGET_EXHAUSTED)
        self.assertEqual(result.usage.input_tokens, 11)

    async def test_elapsed_budget_is_durable_and_extendable(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(worker_turns=[TurnScript(effect=ledger.submit)], reviewer_turns=[])
        supervisor = self.supervisor(ledger, runtime, max_elapsed_seconds=1)
        self.con.execute("UPDATE controller_runs SET started_at='2020-01-01T00:00:00.000Z' WHERE id=?", (supervisor.run_id,))
        result = await supervisor.run_assignment("a1")
        self.assertEqual(result.pause_reason, PauseReason.BUDGET_EXHAUSTED)
        self.assertEqual(runtime.turns_started, 0)

    async def test_uncertain_runtime_outcome_does_not_redispatch(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(worker_turns=[TurnScript(uncertain=True), TurnScript(effect=ledger.submit)], reviewer_turns=[])
        result = await self.supervisor(ledger, runtime).run_assignment("a1")
        self.assertEqual(result.pause_reason, PauseReason.RUNTIME_UNCERTAIN)
        self.assertEqual(runtime.turns_started, 1)

    async def test_blocking_state_does_not_start_reviewer(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(worker_turns=[TurnScript(effect=ledger.block)], reviewer_turns=[])
        result = await self.supervisor(ledger, runtime).run_assignment("a1")
        self.assertEqual(result.pause_reason, PauseReason.BLOCKED)
        self.assertEqual(runtime.sessions_started, 1)

    def test_verdict_requires_exact_criterion_coverage(self):
        payload = accepted_verdict(("c1",))
        self.assertIsNone(parse_verdict(payload, expected_criterion_ids=("c1", "c2"), acceptance_allowed=True))
        payload["criterion_results"].append(dict(payload["criterion_results"][0]))
        self.assertIsNone(parse_verdict(payload, expected_criterion_ids=("c1",), acceptance_allowed=True))

    def test_failed_checks_procedurally_forbid_acceptance(self):
        self.assertIsNone(parse_verdict(accepted_verdict(), expected_criterion_ids=("c1",), acceptance_allowed=False))

    def test_journal_forbids_two_unresolved_turns(self):
        run_id = self.journal.create_run("p1", mode="ASSIGNMENT", config={})
        session = self.journal.create_session(run_id=run_id, project_id="p1", role="WORKER", profile="routine", subject_id="a1", external_thread_id="t1", config_hash="h")
        self.journal.begin_turn(session.id, "worker")
        with self.assertRaises(RuntimeError):
            self.journal.begin_turn(session.id, "worker")

    def test_journal_rejects_a_second_running_controller(self):
        first = self.journal.create_run("p1", mode="ASSIGNMENT", config={})
        with self.assertRaisesRegex(RuntimeError, first):
            self.journal.create_run("p1", mode="ASSIGNMENT", config={})

    def test_journal_does_not_resume_over_another_running_controller(self):
        paused = self.journal.create_run("p1", mode="ASSIGNMENT", config={})
        self.journal.finish_run(paused, "PAUSED")
        active = self.journal.create_run("p1", mode="ASSIGNMENT", config={})
        with self.assertRaisesRegex(RuntimeError, active):
            self.journal.resume_run(paused)

    async def test_worker_broker_exposes_only_worker_operations(self):
        class Service:
            def worker_assignment(self, principal): return {"id": "p1"}, {"id": "a1"}
            def worker_context(self, principal, data): return {"assignment_id": "a1", "dynamic": data}

        socket_path = self.home / "sockets" / "a1" / "worker.sock"
        broker = WorkerBroker(Service(), {"id": "worker"}, socket_path)
        await broker.start()
        try:
            allowed = await asyncio.to_thread(request, str(socket_path), "context", {})
            denied = await asyncio.to_thread(request, str(socket_path), "verify", {})
        finally:
            await broker.close()
        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["data"]["assignment_id"], "a1")
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error"]["code"], "AUTHORIZATION_DENIED")

    def test_worker_broker_path_is_short_and_assignment_scoped(self):
        first = broker_socket_path(self.home / ("nested" * 100), "a1")
        second = broker_socket_path(self.home / ("nested" * 100), "a2")
        self.assertLess(len(str(first).encode()), 104)
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
