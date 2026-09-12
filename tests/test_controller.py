from __future__ import annotations

import tempfile
import unittest
import asyncio
import sqlite3
import json
import os
import subprocess
import sys
from pathlib import Path

from taskledger.controller.fakes import FakeLedger, FakeRuntime, TurnScript, accepted_verdict, rejected_verdict
from taskledger.controller.app_server import AppServerRuntime
from taskledger.controller.journal import Journal
from taskledger.controller.model import ExecutionStatus, PauseReason, RuntimeIdentity, RuntimeTurnHandle, RuntimeTurnResult, SessionRole, SupervisorStatus, Usage
from taskledger.controller.supervisor import ProviderAdmissionStopped, Supervisor, SupervisorConfig, parse_verdict
from taskledger.controller.worker_broker import WorkerBroker, broker_socket_path, request
from taskledger.core import canonical, now, sha256
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

    async def test_pause_latch_blocks_worker_provider_admission(self):
        ledger=FakeLedger();runtime=FakeRuntime(worker_turns=[TurnScript()],reviewer_turns=[])
        run=self.journal.create_run("p1",mode="ASSIGNMENT",config={})
        supervisor=Supervisor(project_id="p1",run_id=run,ledger=ledger,runtime=runtime,journal=self.journal,
            stop_requested=lambda:True)
        result=await supervisor.run_assignment("a1")
        self.assertEqual(result.status,SupervisorStatus.PAUSED)
        self.assertEqual(runtime.turns_started,0)

    async def test_pause_latch_blocks_reviewer_provider_admission(self):
        ledger=FakeLedger();ledger.submit()
        runtime=FakeRuntime(worker_turns=[],reviewer_turns=[TurnScript(structured_output=accepted_verdict())])
        run=self.journal.create_run("p1",mode="ASSIGNMENT",config={})
        supervisor=Supervisor(project_id="p1",run_id=run,ledger=ledger,runtime=runtime,journal=self.journal,
            stop_requested=lambda:True)
        result=await supervisor.run_assignment("a1")
        self.assertEqual(result.status,SupervisorStatus.PAUSED)
        self.assertEqual(runtime.turns_started,0)

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

    async def test_missing_usage_fails_closed_when_token_budget_is_enabled(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(
            worker_turns=[TurnScript(effect=ledger.progress, usage_missing=True), TurnScript(effect=ledger.submit)],
            reviewer_turns=[],
        )
        supervisor = self.supervisor(ledger, runtime, max_total_tokens=100)
        result = await supervisor.run_assignment("a1")
        self.assertEqual(result.pause_reason, PauseReason.BUDGET_EXHAUSTED)
        self.assertEqual(runtime.turns_started, 1)
        self.assertEqual(self.journal.usage_report(run_id=supervisor.run_id)["missing_usage_turns"], 1)

    async def test_reviewer_token_reserve_blocks_worker_but_not_pending_review(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(worker_turns=[TurnScript(effect=ledger.submit)], reviewer_turns=[])
        config = SupervisorConfig(max_total_tokens=100)
        run_id = self.journal.create_run("p1", mode="PROJECT", config={})
        supervisor = Supervisor(
            project_id="p1", run_id=run_id, ledger=ledger, runtime=runtime, journal=self.journal,
            config=config, worker_token_reserve=100, manage_run=False,
        )
        result = await supervisor.run_assignment("a1")
        self.assertEqual(result.pause_reason, PauseReason.BUDGET_EXHAUSTED)
        self.assertEqual(runtime.turns_started, 0)
        self.journal.finish_run(run_id, "PAUSED")

        review_ledger = FakeLedger()
        review_ledger.submit()
        review_runtime = FakeRuntime(worker_turns=[], reviewer_turns=[TurnScript(structured_output=accepted_verdict())])
        review_run = self.journal.create_run("p1", mode="PROJECT", config={})
        reviewed = await Supervisor(
            project_id="p1", run_id=review_run, ledger=review_ledger, runtime=review_runtime,
            journal=self.journal, config=config, worker_token_reserve=100, manage_run=False,
        ).run_assignment("a1")
        self.assertEqual(reviewed.status, SupervisorStatus.INTEGRATED)
        self.assertEqual(review_runtime.turns_started, 1)
        self.journal.finish_run(review_run, "COMPLETED")

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

    async def test_process_crash_at_dispatch_boundaries_recovers_without_redispatch(self):
        program = """
import os
import sys
from pathlib import Path
from taskledger.controller.journal import Journal
from taskledger.controller.model import RuntimeIdentity, RuntimeTurnHandle
from taskledger.db import connect

home = Path(sys.argv[1])
boundary = sys.argv[2]
assignment_id = "a-" + boundary
con = connect(home)
journal = Journal(con, home)
run_id = journal.create_run("p1", mode="ASSIGNMENT", config={})
identity = RuntimeIdentity("fake-routine", "test", "hash-routine", {"type": "workspaceWrite"}, "fake-runtime-v1")
session = journal.create_session(
    run_id=run_id, project_id="p1", role="WORKER", profile="routine", subject_id=assignment_id,
    external_thread_id="thread-crashed", config_hash="config", runtime_identity=identity.as_dict(),
)
turn_id = journal.begin_turn(session.id, "worker")
if boundary == "acknowledged":
    journal.acknowledge_turn(turn_id, RuntimeTurnHandle("thread-crashed", "turn-crashed"))
os._exit(23)
"""
        for boundary in ("intent", "acknowledged"):
            with self.subTest(boundary=boundary):
                result = subprocess.run(
                    [sys.executable, "-c", program, str(self.home), boundary],
                    env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 23, result.stderr)
                run = self.con.execute(
                    "SELECT id FROM controller_runs WHERE state='RUNNING' ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                runtime = FakeRuntime(worker_turns=[TurnScript()], reviewer_turns=[])
                assignment_id = "a-" + boundary
                recovered = await Supervisor(
                    project_id="p1", run_id=run["id"], ledger=FakeLedger(assignment_id=assignment_id), runtime=runtime,
                    journal=self.journal, config=SupervisorConfig(),
                ).run_assignment(assignment_id)
                self.assertEqual(recovered.pause_reason, PauseReason.RUNTIME_UNCERTAIN)
                self.assertEqual(runtime.turns_started, 0)

    async def test_app_server_disconnect_releases_request_and_turn_waiters(self):
        class Process:
            def __init__(self):
                self.stdout = asyncio.StreamReader()

        runtime = AppServerRuntime(repository_root=str(self.home))
        runtime.process = Process()
        request_waiter = asyncio.get_running_loop().create_future()
        turn_waiter = asyncio.get_running_loop().create_future()
        runtime.pending[1] = request_waiter
        runtime.turn_waiters["turn-1"] = turn_waiter
        runtime.process.stdout.feed_eof()
        await runtime._read_messages()
        with self.assertRaisesRegex(RuntimeError, "connection closed"):
            await request_waiter
        with self.assertRaisesRegex(RuntimeError, "connection closed"):
            await turn_waiter

    async def test_nc170_nc171_nc172_app_server_sums_response_usage_once_per_turn(self):
        class Process:
            def __init__(self):
                self.stdout = asyncio.StreamReader()

        runtime = AppServerRuntime(repository_root=str(self.home))
        runtime.process = Process()
        first = {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread", "turnId": "turn",
                "tokenUsage": {
                    "last": {"inputTokens": 10, "cachedInputTokens": 4, "outputTokens": 3, "reasoningOutputTokens": 1},
                    "total": {"inputTokens": 10, "cachedInputTokens": 4, "outputTokens": 3, "reasoningOutputTokens": 1},
                },
            },
        }
        second = {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread", "turnId": "turn",
                "tokenUsage": {
                    "last": {"inputTokens": 8, "cachedInputTokens": 2, "outputTokens": 2, "reasoningOutputTokens": 0},
                    "total": {"inputTokens": 18, "cachedInputTokens": 6, "outputTokens": 5, "reasoningOutputTokens": 1},
                },
            },
        }
        for message in (first, first, second):
            runtime.process.stdout.feed_data((json.dumps(message) + "\n").encode())
        runtime.process.stdout.feed_eof()
        await runtime._read_messages()
        self.assertEqual(runtime.thread_usage["thread"], Usage(18, 6, 5, 1))
        self.assertEqual(runtime.usage_events(RuntimeTurnHandle("thread", "turn")), ())

    def test_dynamic_worker_tools_publish_strict_operation_schemas(self):
        specs = {item["name"]: item["inputSchema"] for item in AppServerRuntime._worker_tool_specs()}
        submit = specs["taskledger_submit"]
        self.assertEqual(
            set(submit["required"]),
            {"summary", "evidence", "risks", "unresolved_questions", "follow_up_work"},
        )
        self.assertFalse(submit["additionalProperties"])
        self.assertEqual(specs["taskledger_artifact_register"]["required"], ["path"])
        self.assertFalse(specs["taskledger_context"]["additionalProperties"])

    async def test_resolved_runtime_identity_is_persisted_and_drift_pauses(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(worker_turns=[TurnScript(effect=ledger.progress)], reviewer_turns=[])
        supervisor = self.supervisor(ledger, runtime, max_worker_turns=1)
        first = await supervisor.run_assignment("a1")
        self.assertEqual(first.pause_reason, PauseReason.MAX_TURNS)
        session = self.journal.find_active_session(project_id="p1", role="WORKER", subject_id="a1")
        self.assertEqual(session.runtime_identity["model"], "fake-routine")
        runtime.identity_overrides[(SessionRole.WORKER, "routine")] = RuntimeIdentity(
            "different-model", "high", "different-hash", {"type": "workspaceWrite", "cwd": "/tmp"}, "fake-runtime-v1"
        )
        self.journal.resume_run(supervisor.run_id)
        resumed = await supervisor.run_assignment("a1")
        self.assertEqual(resumed.pause_reason, PauseReason.CONFIGURATION_DRIFT)
        self.assertEqual(runtime.turns_started, 1)

    async def test_usage_events_are_deduplicated_and_missing_usage_is_explicit(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(
            worker_turns=[TurnScript(effect=ledger.progress, usage=Usage(10, 4, 2, 1)), TurnScript(effect=ledger.submit, usage_missing=True)],
            reviewer_turns=[TurnScript(structured_output=accepted_verdict(), usage=Usage(3, 1, 1, 0))],
        )
        supervisor = self.supervisor(ledger, runtime)
        result = await supervisor.run_assignment("a1")
        self.assertEqual(result.status, SupervisorStatus.INTEGRATED)
        report = self.journal.usage_report(run_id=supervisor.run_id)
        self.assertEqual(report["input_tokens"], 13)
        self.assertEqual(report["output_tokens"], 3)
        self.assertEqual(report["missing_usage_turns"], 1)
        self.assertFalse(report["complete"])
        turn = self.con.execute("SELECT id FROM controller_turns ORDER BY started_at LIMIT 1").fetchone()[0]
        event = {"event_id": "duplicate", "usage": {"input_tokens": 1}, "raw": {"kind": "test"}}
        self.journal.record_usage_events(turn, (event, event))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM controller_usage_events WHERE turn_id=?", (turn,)).fetchone()[0], 0)

    async def test_blocking_state_does_not_start_reviewer(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(worker_turns=[TurnScript(effect=ledger.block)], reviewer_turns=[])
        result = await self.supervisor(ledger, runtime).run_assignment("a1")
        self.assertEqual(result.pause_reason, PauseReason.BLOCKED)
        self.assertEqual(runtime.sessions_started, 1)

    async def test_integration_failure_and_uncertainty_remain_distinct(self):
        for status, reason in (
            (ExecutionStatus.ACCEPTED, PauseReason.INTEGRATION_FAILED),
            (ExecutionStatus.INTEGRATION_UNCERTAIN, PauseReason.INTEGRATION_UNCERTAIN),
        ):
            ledger = FakeLedger(); ledger.status = status
            runtime = FakeRuntime(worker_turns=[], reviewer_turns=[])
            result = await self.supervisor(ledger, runtime).run_assignment("a1")
            self.assertEqual(result.pause_reason, reason)
            self.assertEqual(runtime.turns_started, 0)

    def test_verdict_requires_exact_criterion_coverage(self):
        payload = accepted_verdict(("c1",))
        self.assertIsNone(parse_verdict(payload, expected_criterion_ids=("c1", "c2"), acceptance_allowed=True))
        payload["criterion_results"].append(dict(payload["criterion_results"][0]))
        self.assertIsNone(parse_verdict(payload, expected_criterion_ids=("c1",), acceptance_allowed=True))

    def test_failed_checks_procedurally_forbid_acceptance(self):
        self.assertIsNone(parse_verdict(accepted_verdict(), expected_criterion_ids=("c1",), acceptance_allowed=False))

    def test_reviewer_verdict_contract_rejects_every_inconsistent_acceptance_shape(self):
        expected = ("c1", "c2")
        valid = accepted_verdict(expected)
        self.assertIsNotNone(parse_verdict(valid, expected_criterion_ids=expected, acceptance_allowed=True))
        variants = []
        value = accepted_verdict(expected); value["criterion_results"].pop(); variants.append(value)
        value = accepted_verdict(expected); value["criterion_results"][0]["criterion_id"] = "unknown"; variants.append(value)
        value = accepted_verdict(expected); value["criterion_results"][1]["criterion_id"] = "c1"; variants.append(value)
        value = accepted_verdict(expected); value["criterion_results"][0]["satisfied"] = "yes"; variants.append(value)
        value = accepted_verdict(expected); value["criterion_results"][0]["evidence"] = ""; variants.append(value)
        value = accepted_verdict(expected); value["criterion_results"][0]["satisfied"] = False; variants.append(value)
        value = accepted_verdict(expected); value["behavior_matches_intent"] = False; variants.append(value)
        value = accepted_verdict(expected); value["required_evidence_present"] = False; variants.append(value)
        value = accepted_verdict(expected); value["blocking_issues_remaining"] = True; variants.append(value)
        value = accepted_verdict(expected); value["extra"] = True; variants.append(value)
        for payload in variants:
            self.assertIsNone(parse_verdict(payload, expected_criterion_ids=expected, acceptance_allowed=True), payload)

    def test_rejected_and_blocked_verdicts_require_actionable_semantics(self):
        rejected = rejected_verdict(); rejected["corrections"] = None
        self.assertIsNone(parse_verdict(rejected, expected_criterion_ids=("c1",), acceptance_allowed=True))
        rejected = rejected_verdict(); rejected["criterion_results"][0]["satisfied"] = True; rejected["behavior_matches_intent"] = True
        self.assertIsNone(parse_verdict(rejected, expected_criterion_ids=("c1",), acceptance_allowed=True))
        blocked = accepted_verdict(); blocked.update({"outcome": "BLOCKED", "blocking_issues_remaining": True, "blocker_id": None, "blocker": None})
        self.assertIsNone(parse_verdict(blocked, expected_criterion_ids=("c1",), acceptance_allowed=True))

    def test_journal_forbids_two_unresolved_turns(self):
        run_id = self.journal.create_run("p1", mode="ASSIGNMENT", config={})
        session = self.journal.create_session(run_id=run_id, project_id="p1", role="WORKER", profile="routine", subject_id="a1", external_thread_id="t1", config_hash="h")
        self.journal.begin_turn(session.id, "worker")
        with self.assertRaises(RuntimeError):
            self.journal.begin_turn(session.id, "worker")

    def test_journal_turn_identity_and_idempotency_boundaries(self):
        run_id = self.journal.create_run("p1", mode="ASSIGNMENT", config={})
        session = self.journal.create_session(run_id=run_id, project_id="p1", role="WORKER", profile="routine", subject_id="a1", external_thread_id="thread", config_hash="h")
        local_id = self.journal.begin_turn(session.id, "worker")
        with self.assertRaises(RuntimeError):
            self.journal.acknowledge_turn(local_id, RuntimeTurnHandle("wrong-thread", "turn"))
        handle = RuntimeTurnHandle("thread", "turn")
        self.journal.acknowledge_turn(local_id, handle)
        with self.assertRaises(RuntimeError):
            self.journal.acknowledge_turn(local_id, handle)
        result = RuntimeTurnResult(handle, final_response="done", usage=Usage(1, 0, 2, 1))
        self.journal.complete_turn(local_id, result)
        self.journal.complete_turn(local_id, result)
        with self.assertRaises(RuntimeError):
            self.journal.complete_turn(local_id, RuntimeTurnResult(handle, final_response="different"))
        with self.assertRaises(RuntimeError):
            self.journal.complete_turn(local_id, RuntimeTurnResult(RuntimeTurnHandle("other", "turn")))
        self.journal.consume_turn(local_id)
        self.journal.consume_turn(local_id)

    async def test_nc051_nc052_dispatch_gap_fails_uncertain_without_redispatch(self):
        runtime = FakeRuntime(worker_turns=[], reviewer_turns=[])
        run_id = self.journal.create_run("p1", mode="ASSIGNMENT", config={})
        session = self.journal.create_session(
            run_id=run_id, project_id="p1", role="WORKER", profile="routine",
            subject_id="a1", external_thread_id="thread-1", config_hash="h",
        )
        local_id = self.journal.begin_turn(session.id, "worker")
        problems = await Supervisor(
            project_id="p1", run_id=run_id, ledger=FakeLedger(), runtime=runtime,
            journal=self.journal, manage_run=False,
        ).reconcile_open_turns()
        self.assertEqual(problems, [local_id])
        self.assertEqual(self.con.execute("SELECT state FROM controller_turns WHERE id=?", (local_id,)).fetchone()[0], "UNCERTAIN")
        self.assertEqual(runtime.turns_started, 0)
        self.journal.finish_run(run_id, "PAUSED")

    async def test_nc053_nc054_nc055_nc074_running_or_completed_turn_reconciles_exact_identity(self):
        for initial_state in ("RUNNING", "COMPLETED"):
            runtime = FakeRuntime(worker_turns=[], reviewer_turns=[])
            runtime.roles["thread"] = SessionRole.WORKER
            script = TurnScript(usage=Usage(7, 2, 3, 1))
            runtime.turns[("thread", "external")] = (initial_state, script)
            run_id = self.journal.create_run("p1", mode="ASSIGNMENT", config={})
            session = self.journal.create_session(
                run_id=run_id, project_id="p1", role="WORKER", profile="routine",
                subject_id=f"a-{initial_state}", external_thread_id="thread", config_hash="h",
            )
            local_id = self.journal.begin_turn(session.id, "worker")
            self.journal.acknowledge_turn(local_id, RuntimeTurnHandle("thread", "external"))
            problems = await Supervisor(
                project_id="p1", run_id=run_id, ledger=FakeLedger(), runtime=runtime,
                journal=self.journal, manage_run=False,
            ).reconcile_open_turns()
            self.assertEqual(problems, [])
            terminal = self.journal.terminal_unconsumed_turn(session.id)
            self.assertEqual(terminal.state, "COMPLETED")
            self.assertEqual(terminal.result.handle, RuntimeTurnHandle("thread", "external"))
            self.assertEqual(terminal.result.usage.input_tokens, 7)
            self.journal.finish_run(run_id, "PAUSED")

    async def test_nc056_nc058_terminal_worker_result_is_consumed_before_continuation(self):
        ledger = FakeLedger()
        ledger.progress()
        runtime = FakeRuntime(worker_turns=[TurnScript(effect=ledger.submit)], reviewer_turns=[TurnScript(structured_output=accepted_verdict())])
        run_id = self.journal.create_run("p1", mode="ASSIGNMENT", config={})
        runtime.roles["thread"] = SessionRole.WORKER
        identity = runtime.session_identity(role=SessionRole.WORKER, profile="routine", subject_id="a1", cwd="/tmp", writable=True).as_dict()
        session = self.journal.create_session(
            run_id=run_id, project_id="p1", role="WORKER", profile="routine",
            subject_id="a1", external_thread_id="thread", config_hash=sha256(canonical(identity)),
            runtime_identity=identity,
        )
        local_id = self.journal.begin_turn(session.id, "worker", progress_before="0:ACTIVE:None")
        handle = RuntimeTurnHandle("thread", "completed")
        self.journal.acknowledge_turn(local_id, handle)
        self.journal.complete_turn(local_id, RuntimeTurnResult(handle))
        result = await Supervisor(
            project_id="p1", run_id=run_id, ledger=ledger, runtime=runtime,
            journal=self.journal, manage_run=False, reconcile_runtime=False,
        ).run_assignment("a1")
        self.assertEqual(result.status, SupervisorStatus.INTEGRATED)
        row = self.con.execute("SELECT consumed_at,progress_after,progressed FROM controller_turns WHERE id=?", (local_id,)).fetchone()
        self.assertIsNotNone(row["consumed_at"])
        self.assertIsNotNone(row["progress_after"])
        self.assertEqual(row["progressed"], 1)
        self.journal.finish_run(run_id, "COMPLETED")

    def test_nc057_applied_reviewer_result_is_consumed_without_reapply(self):
        ledger = FakeLedger()
        ledger.status = ExecutionStatus.ACTIVE
        runtime = FakeRuntime(worker_turns=[], reviewer_turns=[])
        run_id = self.journal.create_run("p1", mode="ASSIGNMENT", config={})
        session = self.journal.create_session(
            run_id=run_id, project_id="p1", role="REVIEWER", profile="taskledger_reviewer",
            subject_id="old-submission", external_thread_id="review-thread", config_hash="h",
        )
        local_id = self.journal.begin_turn(session.id, "reviewer")
        handle = RuntimeTurnHandle("review-thread", "review-turn")
        self.journal.acknowledge_turn(local_id, handle)
        self.journal.complete_turn(local_id, RuntimeTurnResult(handle, structured_output=accepted_verdict()))
        supervisor = Supervisor(project_id="p1", run_id=run_id, ledger=ledger, runtime=runtime, journal=self.journal, manage_run=False)
        supervisor.reconcile_resolved_reviewer_sessions()
        self.assertIsNone(self.journal.terminal_unconsumed_turn(session.id))
        self.assertIsNone(self.journal.find_active_session(project_id="p1", role="REVIEWER", subject_id="old-submission"))
        self.assertEqual(ledger.applied, [])
        self.journal.finish_run(run_id, "PAUSED")

    async def test_nc072_nc073_runtime_unknown_and_known_failure_are_distinct(self):
        for runtime_state, expected_state, expected_problems in (
            ("UNKNOWN", "UNCERTAIN", 1),
            ("FAILED", "FAILED", 0),
        ):
            runtime = FakeRuntime(worker_turns=[], reviewer_turns=[])
            runtime.roles["thread"] = SessionRole.WORKER
            script = TurnScript(failure="known failure" if runtime_state == "FAILED" else None, uncertain=runtime_state == "UNKNOWN")
            runtime.turns[("thread", runtime_state)] = (runtime_state, script)
            run_id = self.journal.create_run("p1", mode="ASSIGNMENT", config={})
            session = self.journal.create_session(
                run_id=run_id, project_id="p1", role="WORKER", profile="routine",
                subject_id=f"a-{runtime_state}", external_thread_id="thread", config_hash="h",
            )
            local_id = self.journal.begin_turn(session.id, "worker")
            self.journal.acknowledge_turn(local_id, RuntimeTurnHandle("thread", runtime_state))
            problems = await Supervisor(
                project_id="p1", run_id=run_id, ledger=FakeLedger(), runtime=runtime,
                journal=self.journal, manage_run=False,
            ).reconcile_open_turns()
            self.assertEqual(local_id in problems, bool(expected_problems))
            self.assertEqual(self.con.execute("SELECT state FROM controller_turns WHERE id=?", (local_id,)).fetchone()[0], expected_state)
            self.journal.finish_run(run_id, "PAUSED")

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

    async def test_generic_worker_and_independent_reviewer_capacity(self):
        class TrackingRuntime(FakeRuntime):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.active = {SessionRole.WORKER: 0, SessionRole.REVIEWER: 0}
                self.maximum = dict(self.active)
                self.worker_reviewer_overlap = False

            async def wait_turn(self, handle):
                role = self.roles[handle.thread_id]
                self.active[role] += 1
                self.maximum[role] = max(self.maximum[role], self.active[role])
                self.worker_reviewer_overlap |= all(self.active.values())
                try:
                    await asyncio.sleep(0.02)
                    return await super().wait_turn(handle)
                finally:
                    self.active[role] -= 1

        ledgers = [FakeLedger(assignment_id=f"a{i}") for i in range(3)]
        runtime = TrackingRuntime(
            worker_turns=[TurnScript(effect=ledger.submit) for ledger in ledgers],
            reviewer_turns=[TurnScript(structured_output=accepted_verdict()) for _ in ledgers],
        )
        run_id = self.journal.create_run("p1", mode="PROJECT", config={})
        worker_capacity, reviewer_capacity = asyncio.Semaphore(2), asyncio.Semaphore(1)
        supervisors = [Supervisor(
            project_id="p1", run_id=run_id, ledger=ledger, runtime=runtime, journal=self.journal,
            worker_capacity=worker_capacity, reviewer_capacity=reviewer_capacity, manage_run=False,
            reconcile_runtime=False,
        ) for ledger in ledgers]
        results = await asyncio.gather(*(supervisor.run_assignment(ledger.assignment_id) for supervisor, ledger in zip(supervisors, ledgers)))
        self.assertTrue(all(result.status == SupervisorStatus.INTEGRATED for result in results))
        self.assertEqual(runtime.maximum[SessionRole.WORKER], 2)
        self.assertEqual(runtime.maximum[SessionRole.REVIEWER], 1)
        self.assertTrue(runtime.worker_reviewer_overlap)
        self.journal.finish_run(run_id, "COMPLETED")

    async def test_project_wide_worker_turn_budget_is_not_multiplied_by_assignments(self):
        ledgers = [FakeLedger(assignment_id=f"a{i}") for i in range(2)]
        runtime = FakeRuntime(
            worker_turns=[TurnScript(effect=ledger.progress) for ledger in ledgers],
            reviewer_turns=[],
        )
        run_id = self.journal.create_run("p1", mode="PROJECT", config={})
        capacity = asyncio.Semaphore(2)
        supervisors = [Supervisor(
            project_id="p1", run_id=run_id, ledger=ledger, runtime=runtime, journal=self.journal,
            config=SupervisorConfig(max_worker_turns=5), worker_capacity=capacity,
            run_worker_turn_limit=1, manage_run=False, reconcile_runtime=False,
        ) for ledger in ledgers]
        results = await asyncio.gather(*(item.run_assignment(ledger.assignment_id) for item, ledger in zip(supervisors, ledgers)))
        self.assertEqual(runtime.turns_started, 1)
        self.assertTrue(all(result.pause_reason == PauseReason.BUDGET_EXHAUSTED for result in results))
        self.journal.finish_run(run_id, "PAUSED")

    def test_worker_broker_path_is_short_and_assignment_scoped(self):
        first = broker_socket_path(self.home / ("nested" * 100), "a1")
        second = broker_socket_path(self.home / ("nested" * 100), "a2")
        self.assertLess(len(str(first).encode()), 104)
        self.assertNotEqual(first, second)

    def test_v5_controller_journal_migration_preserves_sessions_turns_and_usage(self):
        legacy_home = self.home / "legacy-controller"
        legacy = connect(legacy_home)
        stamp = now()
        with transaction(legacy):
            legacy.execute(
                "INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("legacy-project", str(legacy_home), str(legacy_home / ".git"), "main", stamp, "ACTIVE", stamp, None, None, stamp, stamp),
            )
            legacy.execute(
                "INSERT INTO controller_runs(id,project_id,mode,state,config_json,created_at,started_at) VALUES(?,?,?,?,?,?,?)",
                ("legacy-run", "legacy-project", "ASSIGNMENT", "PAUSED", "{}", stamp, stamp),
            )
        legacy.close()
        raw = sqlite3.connect(legacy_home / "taskledger.sqlite3")
        raw.executescript("""
            PRAGMA foreign_keys=OFF;
            DROP TABLE controller_usage_events;
            DROP TABLE controller_turns;
            DROP TABLE controller_sessions;
            CREATE TABLE controller_sessions(
             id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES controller_runs(id),project_id TEXT NOT NULL REFERENCES projects(id),
             role TEXT NOT NULL CHECK(role IN ('WORKER','REVIEWER','REQUIREMENT_REVIEWER')),profile TEXT NOT NULL,subject_id TEXT NOT NULL,
             external_thread_id TEXT NOT NULL,config_hash TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('ACTIVE','CLOSED','UNCERTAIN')),
             created_at TEXT NOT NULL,closed_at TEXT);
            CREATE TABLE controller_turns(
             id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES controller_sessions(id),sequence INTEGER NOT NULL,
             state TEXT NOT NULL CHECK(state IN ('DISPATCHING','RUNNING','COMPLETED','FAILED','UNCERTAIN')),prompt_kind TEXT NOT NULL,
             external_turn_id TEXT,result_json TEXT,final_response TEXT,error TEXT,progress_before TEXT,progress_after TEXT,
             progressed INTEGER CHECK(progressed IN (0,1)),input_tokens INTEGER NOT NULL DEFAULT 0,cached_input_tokens INTEGER NOT NULL DEFAULT 0,
             output_tokens INTEGER NOT NULL DEFAULT 0,reasoning_tokens INTEGER NOT NULL DEFAULT 0,
             started_at TEXT NOT NULL,finished_at TEXT,consumed_at TEXT,UNIQUE(session_id,sequence));
            CREATE TABLE controller_usage_events(
             id TEXT PRIMARY KEY,turn_id TEXT NOT NULL REFERENCES controller_turns(id),external_event_id TEXT NOT NULL,raw_json TEXT NOT NULL,
             input_tokens INTEGER,cached_input_tokens INTEGER,output_tokens INTEGER,reasoning_tokens INTEGER,created_at TEXT NOT NULL,
             UNIQUE(turn_id,external_event_id));
            DELETE FROM schema_migrations WHERE version=6;
        """)
        raw.execute(
            "INSERT INTO controller_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("legacy-session", "legacy-run", "legacy-project", "WORKER", "routine", "a1", "thread", "hash", "CLOSED", stamp, stamp),
        )
        raw.execute(
            "INSERT INTO controller_turns VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("legacy-turn", "legacy-session", 1, "COMPLETED", "worker", "external", None, "done", None, None, None, None, 9, 4, 2, 1, stamp, stamp, stamp),
        )
        raw.execute(
            "INSERT INTO controller_usage_events VALUES(?,?,?,?,?,?,?,?,?)",
            ("legacy-usage", "legacy-turn", "event", "{}", 9, 4, 2, 1, stamp),
        )
        raw.commit()
        raw.close()

        migrated = connect(legacy_home)
        self.assertEqual(migrated.execute("SELECT runtime_identity_json FROM controller_sessions WHERE id='legacy-session'").fetchone()[0], "{}")
        self.assertEqual(tuple(migrated.execute("SELECT usage_missing,input_tokens FROM controller_turns WHERE id='legacy-turn'").fetchone()), (0, 9))
        self.assertEqual(migrated.execute("SELECT COUNT(*) FROM controller_usage_events WHERE turn_id='legacy-turn'").fetchone()[0], 1)
        migrated.execute(
            "INSERT INTO controller_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("creator", "legacy-run", "legacy-project", "TASK_CREATOR", "creator", "review", "creator-thread", "hash", "{}", "CLOSED", stamp, stamp),
        )
        self.assertEqual(migrated.execute("PRAGMA foreign_key_check").fetchall(), [])
        migrated.close()


if __name__ == "__main__":
    unittest.main()
