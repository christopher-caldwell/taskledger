from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from taskledger.controller.benchmark import SCHEMA_VERSION, compare_measurements, normalize_measurement
from taskledger.controller.app_server import AppServerRuntime
from taskledger.controller.fakes import FakeLedger, FakeRuntime, TurnScript, accepted_verdict
from taskledger.controller.journal import Journal
from taskledger.controller.model import PauseReason, RuntimeTurnHandle, RuntimeTurnResult, SessionRole, Usage, UsagePrecision
from taskledger.controller.reporting import controller_report
from taskledger.controller.supervisor import Supervisor, SupervisorConfig
from taskledger.core import now
from taskledger.db import connect, transaction
from tests.live_evidence import EvidenceRuntime, LiveEvidence


def measurement(**changes):
    value = {
        "schema_version": SCHEMA_VERSION,
        "outcome": "COMPLETED_VERIFIED",
        "accounting": "COMPLETE",
        "provenance": {"source": "fixture"},
        "workload_identity": "workload-v1",
        "starting_code_identity": "oid",
        "review_standard": "independent-v1",
        "measurement_scope": "POST_APPROVAL_EXECUTION",
        "role_configuration_identity": "roles-v1",
        "usage": {"input_tokens": 1000, "cached_input_tokens": 600, "cache_write_input_tokens": 0, "output_tokens": 80, "reasoning_tokens": 20},
    }
    value.update(changes)
    return value


class BenchmarkReadinessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.con = connect(self.home)
        stamp = now()
        with transaction(self.con):
            self.con.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("p", str(self.home), str(self.home / ".git"), "main", stamp, "ACTIVE", stamp, None, None, stamp, stamp))
        self.journal = Journal(self.con, self.home)

    def tearDown(self):
        self.con.close()
        self.temp.cleanup()

    def _run_and_session(self):
        run = self.journal.create_run("p", mode="PROJECT", config={"manifest": {"scope": "POST_APPROVAL_EXECUTION"}, "limits": {"max_workers": 2, "max_reviewers": 1}})
        session = self.journal.create_session(run_id=run, project_id="p", role=SessionRole.WORKER.value, profile="routine", subject_id="a", external_thread_id="thread", config_hash="h", runtime_identity={"model": "m", "effort": "low", "protocol_identity": "v"})
        return run, session

    async def test_nc225_nc228_all_spend_oracle_and_incomplete_admission(self):
        run, session = self._run_and_session()
        attempts = [
            ("COMPLETED", Usage(1000, 600, 80, 20), UsagePrecision.THREAD_TOTAL_DELTA),
            ("FAILED", Usage(400, 100, 20, 5), UsagePrecision.THREAD_TOTAL_DELTA),
            ("COMPLETED", Usage(600, 300, 50, 10), UsagePrecision.THREAD_TOTAL_DELTA),
        ]
        for index, (outcome, usage, precision) in enumerate(attempts):
            local = self.journal.begin_turn(session.id, "worker")
            handle = RuntimeTurnHandle("thread", f"turn-{index}")
            self.journal.acknowledge_turn(local, handle)
            result = RuntimeTurnResult(handle, usage=usage, usage_precision=precision)
            if outcome == "FAILED":
                self.journal.fail_turn(local, "provider failure", uncertain=False, result=result)
            else:
                self.journal.complete_turn(local, result)
            self.journal.consume_turn(local)
        self.assertEqual(self.journal.usage(run_id=run), Usage(2000, 1000, 150, 35))
        report = await controller_report(self.con, run, include_provider_detail=False)
        self.assertEqual(report["economics"]["totals"]["admission_tokens"], 2150)
        self.assertEqual(report["economics"]["totals"]["non_cached_input_tokens"], 1000)
        self.assertEqual(report["economics"]["by_outcome"]["FAILED"]["input_tokens"], 400)
        missing = self.journal.begin_turn(session.id, "worker")
        self.journal.fail_turn(missing, "lost acknowledgement", uncertain=True)
        self.assertEqual(self.journal.missing_usage_count(run_id=run), 1)
        self.assertEqual((await controller_report(self.con, run, include_provider_detail=False))["quality"]["accounting_complete"], False)

    async def test_r2a_supervisor_accounts_failed_retry_and_blocks_partial_uncertainty(self):
        run = self.journal.create_run("p", mode="ASSIGNMENT", config={})
        ledger = FakeLedger()
        runtime = FakeRuntime(
            worker_turns=[
                TurnScript(effect=ledger.progress, usage=Usage(1000, 600, 80)),
                TurnScript(failure="failed after model work", usage=Usage(400, 100, 20)),
                TurnScript(effect=ledger.submit, usage=Usage(600, 300, 50)),
            ],
            reviewer_turns=[TurnScript(structured_output=accepted_verdict())],
        )
        result = await Supervisor(
            project_id="p",
            run_id=run,
            ledger=ledger,
            runtime=runtime,
            journal=self.journal,
            config=SupervisorConfig(max_worker_turns=4, max_total_tokens=10_000),
        ).run_assignment("a1")
        self.assertEqual(result.status.value, "INTEGRATED")
        self.assertEqual(self.journal.usage(run_id=run), Usage(2000, 1000, 150))
        failed = self.con.execute(
            "SELECT t.id,s.external_thread_id,t.external_turn_id,t.input_tokens,t.output_tokens "
            "FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id "
            "WHERE s.run_id=? AND t.state='FAILED'",
            (run,),
        ).fetchone()
        replay = RuntimeTurnResult(
            RuntimeTurnHandle(failed["external_thread_id"], failed["external_turn_id"]),
            usage=Usage(400, 100, 20),
            usage_precision=UsagePrecision.THREAD_TOTAL_DELTA,
        )
        self.journal.reconcile_usage(failed["id"], replay)
        self.journal.reconcile_usage(failed["id"], replay)
        report = await controller_report(self.con, run, include_provider_detail=False)
        self.assertEqual(report["economics"]["totals"]["admission_tokens"], 2150)
        self.assertEqual(report["economics"]["by_outcome"]["FAILED"]["input_tokens"], 400)
        for dimension in ("by_model", "by_role", "by_dispatch_reason", "by_outcome", "by_accounting_class"):
            for field in ("turn_count", "input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_tokens"):
                self.assertEqual(
                    sum(bucket[field] for bucket in report["economics"][dimension].values()),
                    report["economics"]["totals"][field],
                )

        uncertain_run = self.journal.create_run("p", mode="ASSIGNMENT", config={})
        uncertain_ledger = FakeLedger()
        uncertain_runtime = FakeRuntime(
            worker_turns=[TurnScript(uncertain=True, usage=Usage(40, 10, 2), usage_missing=True)],
            reviewer_turns=[],
        )
        uncertain = await Supervisor(
            project_id="p",
            run_id=uncertain_run,
            ledger=uncertain_ledger,
            runtime=uncertain_runtime,
            journal=self.journal,
            config=SupervisorConfig(max_total_tokens=100),
        ).run_assignment("a1")
        self.assertEqual(uncertain.pause_reason, PauseReason.RUNTIME_UNCERTAIN)
        self.assertEqual(self.journal.usage(run_id=uncertain_run), Usage(40, 10, 2))
        self.assertEqual(self.journal.missing_usage_count(run_id=uncertain_run), 1)
        second = await Supervisor(
            project_id="p",
            run_id=uncertain_run,
            ledger=uncertain_ledger,
            runtime=uncertain_runtime,
            journal=self.journal,
            config=SupervisorConfig(max_total_tokens=100),
        ).run_assignment("a1")
        self.assertEqual(second.pause_reason, PauseReason.RUNTIME_UNCERTAIN)
        self.assertEqual(uncertain_runtime.turns_started, 1)

    async def test_nc229_nc231_breakdowns_reconcile_for_every_dimension(self):
        run, session = self._run_and_session()
        local = self.journal.begin_turn(session.id, "worker")
        handle = RuntimeTurnHandle("thread", "turn")
        self.journal.acknowledge_turn(local, handle)
        self.journal.complete_turn(local, RuntimeTurnResult(handle, usage=Usage(9, 4, 3, 1, 2), usage_precision=UsagePrecision.THREAD_TOTAL_DELTA))
        report = await controller_report(self.con, run, include_provider_detail=False)
        totals = report["economics"]["totals"]
        for dimension in ("by_model", "by_role", "by_dispatch_reason", "by_outcome", "by_accounting_class"):
            for field in ("turn_count", "input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_tokens"):
                self.assertEqual(sum(bucket[field] for bucket in report["economics"][dimension].values()), totals[field])

    async def test_nc226_failed_adapter_inspection_retains_usage(self):
        class ScriptedRuntime(AppServerRuntime):
            async def _ensure_started(self):
                return None

            async def _request(self, method, params):
                self.thread_usage["thread"] = Usage(1400, 700, 100, 25)
                return {"thread": {"turns": [{"id": "turn", "status": "failed", "error": {"message": "boom"}}]}}

        runtime = ScriptedRuntime(repository_root=str(self.home))
        runtime.turn_usage_before["turn"] = Usage(1000, 600, 80, 20)
        inspection = await runtime.inspect_turn(RuntimeTurnHandle("thread", "turn"))
        self.assertEqual(inspection.state, "FAILED")
        self.assertEqual(inspection.result.usage, Usage(400, 100, 20, 5))
        self.assertEqual(inspection.result.usage_precision, UsagePrecision.THREAD_TOTAL_DELTA)

    async def test_r2a_unknown_adapter_inspection_retains_partial_usage(self):
        class ScriptedRuntime(AppServerRuntime):
            async def _ensure_started(self):
                return None

            async def _request(self, method, params):
                raise RuntimeError("connection lost")

        runtime = ScriptedRuntime(repository_root=str(self.home), experimental_raw_events=True)
        runtime.turn_exact_usage["turn"] = Usage(40, 10, 2, 1)
        inspection = await runtime.inspect_turn(RuntimeTurnHandle("thread", "turn"))
        self.assertEqual(inspection.state, "UNKNOWN")
        self.assertEqual(inspection.result.usage, Usage(40, 10, 2, 1))
        self.assertTrue(inspection.result.usage_missing)
        self.assertEqual(inspection.result.usage_precision, UsagePrecision.PARTIAL_OBSERVATION)

    async def test_nc227_partial_then_final_reconciliation_is_idempotent(self):
        run, session = self._run_and_session()
        local = self.journal.begin_turn(session.id, "worker")
        handle = RuntimeTurnHandle("thread", "turn")
        self.journal.acknowledge_turn(local, handle)
        partial = RuntimeTurnResult(handle, usage=Usage(100, 20, 10, 2), usage_missing=True, usage_precision=UsagePrecision.PARTIAL_OBSERVATION)
        self.journal.fail_turn(local, "disconnect", uncertain=True, result=partial)
        self.assertEqual(self.journal.usage(run_id=run).input_tokens, 100)
        final = RuntimeTurnResult(handle, usage=Usage(140, 30, 12, 3), usage_precision=UsagePrecision.THREAD_TOTAL_DELTA, cumulative_before=Usage(), cumulative_after=Usage(140, 30, 12, 3))
        self.journal.complete_turn(local, final)
        self.journal.complete_turn(local, final)
        self.assertEqual(self.journal.usage(run_id=run), Usage(140, 30, 12, 3))
        self.assertEqual(self.journal.missing_usage_count(run_id=run), 0)
        self.assertEqual(self.con.execute("SELECT state FROM controller_turns WHERE id=?", (local,)).fetchone()[0], "COMPLETED")

    async def test_r2b_live_evidence_survives_assertion_and_enrichment_failure(self):
        with tempfile.TemporaryDirectory() as outer:
            evidence_dir = Path(outer) / "evidence"
            disposable = Path(outer) / "disposable"
            disposable.mkdir()
            evidence = LiveEvidence(
                directory=evidence_dir,
                validation_id="r2b-failure",
                source_root=Path(__file__).parents[1],
                model="fake-luna",
                effort="low",
                configuration={"max_turns": 1},
                attempt_id="attempt",
            )
            run, session = self._run_and_session()
            local = self.journal.begin_turn(session.id, "worker")
            handle = RuntimeTurnHandle("thread", "turn-known")
            self.journal.acknowledge_turn(local, handle)
            result = RuntimeTurnResult(
                handle,
                usage=Usage(100, 20, 10, 2),
                usage_precision=UsagePrecision.THREAD_TOTAL_DELTA,
            )
            self.journal.complete_turn(local, result)
            evidence.session(role="WORKER", subject_id="a", thread_id="thread", resumed=False)
            evidence.turn_started(thread_id="thread", turn_id="turn-known")
            evidence.turn_result(result, outcome="COMPLETED")
            original = AssertionError("intentional post-usage assertion")

            async def unavailable(_thread):
                raise RuntimeError("provider enrichment unavailable")

            await evidence.finalize(
                con=self.con,
                run_id=run,
                outcome="FAILED",
                failure=original,
                provider_loader=unavailable,
            )
            shutil.rmtree(disposable)
            retained = json.loads(evidence.path.read_text())
            self.assertEqual(retained["failure"]["message"], str(original))
            self.assertEqual(retained["sessions"][0]["session_id"], session.id)
            self.assertEqual(retained["usage_allocations"][0]["input_tokens"], 100)
            self.assertEqual(retained["report"]["economics"]["totals"]["admission_tokens"], 110)
            self.assertEqual(retained["report"]["quality"]["provider_detail"]["status"], "UNAVAILABLE")

    async def test_r2b_evidence_observer_uses_acknowledged_runtime_identity(self):
        with tempfile.TemporaryDirectory() as outer:
            evidence = LiveEvidence(
                directory=Path(outer) / "evidence",
                validation_id="r2b-observer",
                source_root=Path(__file__).parents[1],
                model="fake-luna",
                effort="low",
                configuration={"max_turns": 1},
                attempt_id="observer",
            )
            observed = []
            runtime = EvidenceRuntime(
                FakeRuntime(worker_turns=[TurnScript()], reviewer_turns=[]),
                evidence,
                on_result=lambda _result, identity: observed.append(dict(identity)),
            )
            session = await runtime.start_session(
                role=SessionRole.WORKER,
                profile="routine",
                subject_id="assignment-a",
                cwd=outer,
                writable=True,
            )
            handle = await runtime.start_turn(thread_id=session.thread_id, prompt="bounded")
            await runtime.wait_turn(handle)
            self.assertEqual(observed, [{"role": "WORKER", "subject_id": "assignment-a"}])

    async def test_nc232_nc235_fixed_clock_pause_utilization_and_open_wait(self):
        run = self.journal.create_run("p", mode="PROJECT", config={"limits": {"max_workers": 2, "max_reviewers": 1}})
        sessions = [
            self.journal.create_session(run_id=run, project_id="p", role=SessionRole.WORKER.value, profile="routine", subject_id=subject, external_thread_id=f"thread-{subject}", config_hash="h", runtime_identity={})
            for subject in ("a", "b")
        ]
        creator = self.journal.create_session(run_id=run, project_id="p", role=SessionRole.TASK_CREATOR.value, profile="taskledger_task_creator", subject_id="plan", external_thread_id="thread-plan", config_hash="h", runtime_identity={})
        for index, session in enumerate([*sessions, creator]):
            local = self.journal.begin_turn(session.id, "worker" if index < 2 else "task-creator")
            handle = RuntimeTurnHandle(session.thread_id, f"turn-{index}")
            self.journal.acknowledge_turn(local, handle)
            self.journal.complete_turn(local, RuntimeTurnResult(handle, usage_precision=UsagePrecision.THREAD_TOTAL_DELTA))
            start, end = (("2026-01-01T00:15:00Z", "2026-01-01T00:20:00Z") if index < 2 else ("2026-01-01T00:00:00Z", "2026-01-01T00:05:00Z"))
            self.con.execute("UPDATE controller_turns SET started_at=?,finished_at=? WHERE id=?", (start, end, local))
        self.con.execute("UPDATE controller_runs SET started_at='2026-01-01T00:00:00Z',finished_at='2026-01-01T00:20:00Z',state='COMPLETED' WHERE id=?", (run,))
        self.con.execute("DELETE FROM controller_events WHERE run_id=?", (run,))
        for sequence, scope_type, scope_id, event_type, reason, stamp in (
            (1, "RUN", run, "RUN_STARTED", "INITIAL_START", "2026-01-01T00:00:00Z"),
            (2, "RUN", run, "RUN_PAUSED", "BLOCKED", "2026-01-01T00:05:00Z"),
            (3, "RUN", run, "RUN_RESUMED", "EXPLICIT_RESUME", "2026-01-01T00:15:00Z"),
            (4, "TARGET", "task", "TARGET_WAITING", "WAITING_FOR_INFLIGHT_TARGET", "2026-01-01T00:18:00Z"),
        ):
            self.con.execute("INSERT INTO controller_events(sequence,run_id,scope_type,scope_id,event_type,reason_code,small_attributes_json,occurred_at) VALUES(?,?,?,?,?,?,?,?)", (sequence, run, scope_type, scope_id, event_type, reason, "{}", stamp))
        report = await controller_report(self.con, run, include_provider_detail=False)
        scheduler = report["scheduler"]
        self.assertEqual(scheduler["gross_elapsed_ms"], 20 * 60_000)
        self.assertEqual(scheduler["recorded_paused_ms"], 10 * 60_000)
        self.assertEqual(scheduler["non_paused_wall_ms"], 10 * 60_000)
        self.assertEqual(scheduler["worker_active_interval_union_ms"], 5 * 60_000)
        self.assertEqual(scheduler["reviewer_active_interval_union_ms"], 0)
        self.assertEqual(scheduler["concurrency"]["worker_slot_utilization"], 0.5)
        self.assertEqual(scheduler["waits_entity_ms"]["WAITING_FOR_INFLIGHT_TARGET"], 2 * 60_000)

    async def test_provider_history_classifies_missing_unexpected_and_later_turns(self):
        run, session = self._run_and_session()
        local = self.journal.begin_turn(session.id, "worker")
        handle = RuntimeTurnHandle("thread", "expected-missing")
        self.journal.acknowledge_turn(local, handle)
        self.journal.complete_turn(local, RuntimeTurnResult(handle, usage_precision=UsagePrecision.THREAD_TOTAL_DELTA))
        self.con.execute("UPDATE controller_runs SET started_at='2026-01-01T00:00:00Z',finished_at='2026-01-01T00:10:00Z',state='COMPLETED' WHERE id=?", (run,))

        async def loader(_thread):
            return {
                "turns": [
                    {"id": "unexpected", "createdAt": "2026-01-01T00:05:00Z", "durationMs": 999},
                    {"id": "later", "createdAt": "2026-01-01T00:20:00Z", "durationMs": 999},
                ],
                "items": [{"turnId": "later", "item": {"type": "commandExecution", "durationMs": 999}}],
            }

        report = await controller_report(self.con, run, include_provider_detail=True, provider_loader=loader)
        provider = report["quality"]["provider_detail"]
        self.assertEqual(provider["missing_recorded_turn_ids"], ["expected-missing"])
        self.assertEqual(provider["unexpected_in_window_turn_ids"], ["unexpected"])
        self.assertEqual(provider["unrelated_later_turn_count"], 1)
        self.assertEqual(report["context_efficiency"]["provider_activity"]["command_count"], 0)

    def test_nc239_nc241_capability_identity_is_stable_sanitized_and_sensitive(self):
        agent_dir = self.home / ".codex" / "agents"
        agent_dir.mkdir(parents=True)
        (agent_dir / "taskledger-worker-routine.toml").write_text(
            'model="m"\nmodel_reasoning_effort="low"\ndeveloper_instructions="SENTINEL_SECRET"\n'
        )
        runtime = AppServerRuntime(repository_root=str(self.home), worker_tools={"a": lambda action, data: {}})
        runtime._worker_writable_roots = lambda cwd: [str(self.home)]
        first = runtime.session_identity(role=SessionRole.WORKER, profile="routine", subject_id="a", cwd=str(self.home), writable=True)
        second = runtime.session_identity(role=SessionRole.WORKER, profile="routine", subject_id="a", cwd=str(self.home), writable=True)
        reviewer = runtime.session_identity(role=SessionRole.WORKER, profile="routine", subject_id="a", cwd=str(self.home), writable=False)
        self.assertEqual(first.capability_policy_hash, second.capability_policy_hash)
        self.assertNotEqual(first.capability_policy_hash, reviewer.capability_policy_hash)
        self.assertGreater(first.base_instruction_bytes, 0)
        self.assertGreater(first.profile_instruction_bytes, 0)
        self.assertGreater(first.dynamic_tool_count, 0)
        self.assertNotIn("SENTINEL_SECRET", json.dumps(first.as_dict(), sort_keys=True))

    def test_nc242_nc244_deterministic_qualified_comparison(self):
        left = measurement()
        right = measurement(usage={"input_tokens": 600, "cached_input_tokens": 300, "cache_write_input_tokens": 0, "output_tokens": 50, "reasoning_tokens": 10})
        self.assertEqual(compare_measurements(left, right), compare_measurements(left, right))
        self.assertEqual(compare_measurements(left, right)["comparability"], "MATCHED")
        cheap = measurement(outcome="INCOMPLETE", accounting="PARTIAL_MISSING")
        compared = compare_measurements(cheap, right)
        self.assertIsNone(compared["winner"])
        self.assertTrue(any("outcomes differ" in warning for warning in compared["warnings"]))
        unmatched = measurement(measurement_scope="DIFFERENT")
        self.assertEqual(compare_measurements(left, unmatched)["comparability"], "QUALIFIED_DESCRIPTIVE")
        unavailable = measurement(starting_code_identity=None)
        self.assertEqual(compare_measurements(left, unavailable)["comparability"], "INSUFFICIENT_EVIDENCE")

    def test_nc238_invalid_cache_vector_is_rejected(self):
        bad = measurement(usage={"input_tokens": 1, "cached_input_tokens": 2, "cache_write_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0})
        with self.assertRaisesRegex(ValueError, "cached input"):
            normalize_measurement(bad)

    def test_protocol_is_frozen_and_does_not_authorize_representative_run(self):
        protocol = json.loads((Path(__file__).parents[1] / "docs" / "BENCHMARK_PROTOCOL_V1.json").read_text())
        self.assertEqual(protocol["status"], "FROZEN_BEFORE_FIRST_RUN")
        self.assertFalse(protocol["representative_run_authorized"])
