from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from taskledger.core import canonical, sha256

from .journal import AgentSession, Journal
from .model import (
    ExecutionStatus,
    PauseReason,
    ReviewOutcome,
    ReviewPreparation,
    ReviewVerdict,
    RuntimeTurnHandle,
    DispatchReason,
    PromptPacket,
    SessionRole,
    SupervisorResult,
    SupervisorStatus,
    TurnExecution,
    TurnExecutionState,
    Usage,
)
from .ports import AgentRuntime, LedgerPort


class ConfigurationDrift(RuntimeError):
    pass


class ProviderAdmissionStopped(RuntimeError):
    """A stop latch closed immediately before a spend-bearing dispatch."""


@dataclass(frozen=True)
class SupervisorConfig:
    max_worker_turns: int = 20
    max_consecutive_stalled_turns: int = 2
    max_consecutive_runtime_failures: int = 2
    max_reviewer_turns_per_submission: int = 2
    max_total_tokens: int | None = None
    max_elapsed_seconds: int = 3600
    turn_timeout_seconds: int = 1800
    reviewer_profile: str = "taskledger_reviewer"

    def validate(self) -> None:
        values = (
            self.max_worker_turns,
            self.max_consecutive_stalled_turns,
            self.max_consecutive_runtime_failures,
            self.max_reviewer_turns_per_submission,
            self.max_elapsed_seconds,
            self.turn_timeout_seconds,
        )
        if any(value <= 0 for value in values):
            raise ValueError("controller turn and retry limits must be positive")
        if self.max_total_tokens is not None and self.max_total_tokens <= 0:
            raise ValueError("max_total_tokens must be positive")


class Supervisor:
    """Deterministically supervise one existing Taskledger assignment."""

    def __init__(
        self,
        *,
        project_id: str,
        run_id: str,
        ledger: LedgerPort,
        runtime: AgentRuntime,
        journal: Journal,
        config: SupervisorConfig | None = None,
        worker_capacity: asyncio.Semaphore | None = None,
        reviewer_capacity: asyncio.Semaphore | None = None,
        run_worker_turn_limit: int | None = None,
        run_reviewer_turn_limit: int | None = None,
        worker_token_reserve: int = 0,
        manage_run: bool = True,
        reconcile_runtime: bool = True,
        stop_requested=None,
        owns_project_lease: bool | None = None,
    ) -> None:
        self.project_id = project_id
        self.run_id = run_id
        self.ledger = ledger
        self.runtime = runtime
        self.journal = journal
        self.config = config or SupervisorConfig()
        self.worker_capacity = worker_capacity
        self.reviewer_capacity = reviewer_capacity
        self.run_worker_turn_limit = run_worker_turn_limit
        self.run_reviewer_turn_limit = run_reviewer_turn_limit
        self.worker_token_reserve = worker_token_reserve
        self.manage_run = manage_run
        self.reconcile_runtime = reconcile_runtime
        self.stop_requested = stop_requested or (lambda: False)
        self.owns_project_lease = manage_run if owns_project_lease is None else owns_project_lease
        self.config.validate()

    async def reconcile_open_turns(self) -> list[str]:
        problems: list[str] = []
        for turn in self.journal.open_turns(project_id=self.project_id):
            if turn.external_turn_id is None:
                if turn.state != "UNCERTAIN":
                    self.journal.fail_turn(turn.id, "dispatch outcome unknown", uncertain=True)
                problems.append(turn.id)
                continue
            inspection = await self.runtime.inspect_turn(RuntimeTurnHandle(turn.thread_id, turn.external_turn_id))
            if inspection.state == "COMPLETED" and inspection.result is not None:
                self.journal.record_usage_events(turn.id, self.runtime.usage_events(inspection.result.handle))
                self.journal.complete_turn(turn.id, inspection.result)
            elif inspection.state == "FAILED":
                self.journal.fail_turn(turn.id, inspection.error or "external turn failed", uncertain=False, result=inspection.result)
            elif inspection.state == "RUNNING":
                handle = RuntimeTurnHandle(turn.thread_id, turn.external_turn_id)
                try:
                    result = await asyncio.wait_for(self.runtime.wait_turn(handle), timeout=self.config.turn_timeout_seconds)
                except Exception as exc:
                    final = await self.runtime.inspect_turn(handle)
                    if final.state == "COMPLETED" and final.result is not None:
                        result = final.result
                    elif final.state == "FAILED":
                        self.journal.fail_turn(turn.id, final.error or str(exc), uncertain=False, result=final.result)
                        continue
                    else:
                        self.journal.fail_turn(turn.id, final.error or str(exc), uncertain=True)
                        problems.append(turn.id)
                        continue
                self.journal.record_usage_events(turn.id, self.runtime.usage_events(result.handle))
                self.journal.complete_turn(turn.id, result)
            else:
                self.journal.fail_turn(turn.id, inspection.error or "external turn outcome unknown", uncertain=True)
                problems.append(turn.id)
        return problems

    async def reconcile_terminal_usage(self) -> list[str]:
        """Repair only terminal allocations with a unique local and provider boundary."""
        unresolved: list[str] = []
        for candidate in self.journal.terminal_missing_usage_candidates(run_id=self.run_id):
            role = SessionRole(candidate["role"])
            try:
                if role == SessionRole.WORKER:
                    cwd = self.ledger.worker_cwd(candidate["subject_id"])
                    writable = True
                elif role == SessionRole.REVIEWER:
                    cwd = (
                        self.ledger.checkpoint_reviewer_cwd(candidate["subject_id"])
                        if candidate["prompt_kind"] == "checkpoint-reviewer"
                        else self.ledger.reviewer_cwd(candidate["subject_id"])
                    )
                    writable = False
                else:
                    cwd = self.ledger.project["repository_root"]
                    writable = False
                identity = self.runtime.session_identity(
                    role=role, profile=candidate["profile"], subject_id=candidate["subject_id"],
                    cwd=cwd, writable=writable,
                ).as_dict()
                if identity != candidate["runtime_identity"]:
                    unresolved.append(candidate["local_turn_id"])
                    continue
                result = await self.runtime.recover_terminal_usage(
                    handle=RuntimeTurnHandle(candidate["thread_id"], candidate["turn_id"]),
                    previous_turn_id=candidate["previous_turn_id"],
                    cumulative_before=candidate["cumulative_before"],
                    target_is_first=candidate["target_is_first"], role=role,
                    profile=candidate["profile"], subject_id=candidate["subject_id"],
                    cwd=cwd, writable=writable,
                )
                if result is None:
                    unresolved.append(candidate["local_turn_id"])
                    continue
                self.journal.reconcile_usage(candidate["local_turn_id"], result)
            except Exception:
                unresolved.append(candidate["local_turn_id"])
        return unresolved

    def reconcile_resolved_reviewer_sessions(self) -> None:
        for session in self.journal.active_sessions(project_id=self.project_id, role=SessionRole.REVIEWER.value):
            turn = self.journal.terminal_unconsumed_turn(session.id)
            pending = (
                self.ledger.checkpoint_pending(session.subject_id)
                if turn is not None and turn.prompt_kind == "checkpoint-reviewer"
                else self.ledger.submission_pending(session.subject_id)
            )
            if pending:
                continue
            if turn is not None and turn.state in {"COMPLETED", "FAILED"}:
                self.journal.consume_turn(turn.id)
            self.journal.close_session(session.id)

    async def run_assignment(self, assignment_id: str) -> SupervisorResult:
        if self.owns_project_lease:
            with self.journal.project_lock(self.project_id):
                return await self._run_assignment(assignment_id)
        return await self._run_assignment(assignment_id)

    async def _run_assignment(self, assignment_id: str) -> SupervisorResult:
            new_worker_turns = 0
            new_reviewer_turns = 0
            reviews = 0
            try:
                if self.reconcile_runtime:
                    recovery = await self.reconcile_open_turns()
                    if recovery:
                        return self._pause(assignment_id, PauseReason.RUNTIME_UNCERTAIN, f"unresolved external turns: {', '.join(recovery)}")
                    await self.reconcile_terminal_usage()
                    self.reconcile_resolved_reviewer_sessions()

                while True:
                    view = self.ledger.execution_view(assignment_id)
                    worker = self.journal.find_active_session(project_id=self.project_id, role=SessionRole.WORKER.value, subject_id=assignment_id)
                    if worker is not None:
                        terminal = self.journal.terminal_unconsumed_turn(worker.id)
                        if terminal is not None:
                            if terminal.state == "FAILED":
                                self.journal.consume_turn(terminal.id)
                                if view.status == ExecutionStatus.ACTIVE and self.journal.consecutive_failures(worker.id) >= self.config.max_consecutive_runtime_failures:
                                    return self._pause(assignment_id, PauseReason.RUNTIME_FAILED, "worker runtime failure budget exhausted", new_worker_turns, new_reviewer_turns, reviews)
                            else:
                                after = self.ledger.progress_fingerprint(assignment_id)
                                self.journal.consume_turn(terminal.id, progress_after=after)
                                if view.status == ExecutionStatus.ACTIVE and self.journal.consecutive_stalled_turns(worker.id) >= self.config.max_consecutive_stalled_turns:
                                    return self._pause(assignment_id, PauseReason.STALLED, "worker made no durable progress", new_worker_turns, new_reviewer_turns, reviews)
                            view = self.ledger.execution_view(assignment_id)

                    result = self._terminal_result(assignment_id, view, new_worker_turns, new_reviewer_turns, reviews)
                    if result is not None:
                        return result

                    if view.status == ExecutionStatus.SUBMITTED:
                        if not view.pending_submission_id:
                            return self._pause(assignment_id, PauseReason.INVALID_STATE, "submitted assignment has no pending submission", new_worker_turns, new_reviewer_turns, reviews)
                        if self._elapsed_budget_exhausted() or self._token_budget_exhausted():
                            return self._pause(assignment_id, PauseReason.BUDGET_EXHAUSTED, "controller admission budget exhausted", new_worker_turns, new_reviewer_turns, reviews)
                        try:
                            preparation = self.ledger.prepare_review(view.pending_submission_id)
                        except Exception as exc:
                            return self._pause(assignment_id, PauseReason.INVALID_STATE, f"submission is not safe to review: {exc}", new_worker_turns, new_reviewer_turns, reviews)
                        try:
                            if self._run_reviewer_budget_exhausted():
                                return self._pause(assignment_id, PauseReason.BUDGET_EXHAUSTED, "project reviewer turn budget exhausted", new_worker_turns, new_reviewer_turns, reviews)
                            verdict, turn_id, started, error = await self._review(preparation)
                        except ConfigurationDrift as exc:
                            return self._pause(assignment_id, PauseReason.CONFIGURATION_DRIFT, str(exc), new_worker_turns, new_reviewer_turns, reviews)
                        new_reviewer_turns += started
                        if error == TurnExecutionState.UNCERTAIN:
                            return self._pause(assignment_id, PauseReason.RUNTIME_UNCERTAIN, "reviewer outcome is uncertain", new_worker_turns, new_reviewer_turns, reviews)
                        if verdict is None or turn_id is None:
                            return self._pause(assignment_id, PauseReason.REVIEWER_FAILURE, "reviewer verdict budget exhausted", new_worker_turns, new_reviewer_turns, reviews)
                        try:
                            self.ledger.apply_review(preparation.submission_id, verdict)
                        except Exception as exc:
                            post_error_view = self.ledger.execution_view(assignment_id)
                            classified = self._terminal_result(
                                assignment_id, post_error_view, new_worker_turns, new_reviewer_turns, reviews
                            )
                            if classified is not None:
                                return classified
                            return self._pause(assignment_id, PauseReason.INVALID_STATE, f"ledger refused reviewer verdict: {exc}", new_worker_turns, new_reviewer_turns, reviews)
                        self.journal.consume_turn(turn_id)
                        reviewer = self.journal.find_active_session(project_id=self.project_id, role=SessionRole.REVIEWER.value, subject_id=preparation.submission_id)
                        if reviewer:
                            self.journal.close_session(reviewer.id)
                        reviews += 1
                        continue

                    if view.status == ExecutionStatus.CHECKPOINT:
                        if not view.pending_checkpoint_id:
                            return self._pause(assignment_id, PauseReason.INVALID_STATE, "checkpoint state has no pending checkpoint", new_worker_turns, new_reviewer_turns, reviews)
                        if self._elapsed_budget_exhausted() or self._token_budget_exhausted():
                            return self._pause(assignment_id, PauseReason.BUDGET_EXHAUSTED, "controller admission budget exhausted", new_worker_turns, new_reviewer_turns, reviews)
                        try:
                            preparation = self.ledger.prepare_checkpoint_review(view.pending_checkpoint_id)
                            if self._run_reviewer_budget_exhausted():
                                return self._pause(assignment_id, PauseReason.BUDGET_EXHAUSTED, "project reviewer turn budget exhausted", new_worker_turns, new_reviewer_turns, reviews)
                            verdict, turn_id, started, error = await self._review(preparation, review_kind="checkpoint")
                        except ConfigurationDrift as exc:
                            return self._pause(assignment_id, PauseReason.CONFIGURATION_DRIFT, str(exc), new_worker_turns, new_reviewer_turns, reviews)
                        except Exception as exc:
                            return self._pause(assignment_id, PauseReason.INVALID_STATE, f"checkpoint is not safe to review: {exc}", new_worker_turns, new_reviewer_turns, reviews)
                        new_reviewer_turns += started
                        if error == TurnExecutionState.UNCERTAIN:
                            return self._pause(assignment_id, PauseReason.RUNTIME_UNCERTAIN, "checkpoint reviewer outcome is uncertain", new_worker_turns, new_reviewer_turns, reviews)
                        if verdict is None or turn_id is None:
                            return self._pause(assignment_id, PauseReason.REVIEWER_FAILURE, "checkpoint reviewer verdict budget exhausted", new_worker_turns, new_reviewer_turns, reviews)
                        try:
                            self.ledger.apply_checkpoint_review(preparation.submission_id, verdict)
                        except Exception as exc:
                            if not self.ledger.checkpoint_pending(preparation.submission_id):
                                self.journal.consume_turn(turn_id)
                                self._close_reviewer(preparation.submission_id)
                                reviews += 1
                                continue
                            return self._pause(assignment_id, PauseReason.INVALID_STATE, f"ledger refused checkpoint verdict: {exc}", new_worker_turns, new_reviewer_turns, reviews)
                        self.journal.consume_turn(turn_id)
                        self._close_reviewer(preparation.submission_id)
                        reviews += 1
                        continue

                    if view.status != ExecutionStatus.ACTIVE:
                        return self._pause(assignment_id, PauseReason.INVALID_STATE, f"unexpected execution status {view.status.value}", new_worker_turns, new_reviewer_turns, reviews)

                    if self.worker_capacity is None:
                        worker_result = await self._run_worker_turn(assignment_id, view.profile)
                    else:
                        if self.worker_capacity.locked():
                            self.journal.append_event(run_id=self.run_id,scope_type="ASSIGNMENT",scope_id=assignment_id,event_type="TARGET_WAITING_FOR_WORKER_CAPACITY",reason_code="WAITING_FOR_WORKER_CAPACITY",transition_only=True)
                        async with self.worker_capacity:
                            self.journal.append_event(run_id=self.run_id,scope_type="ASSIGNMENT",scope_id=assignment_id,event_type="TARGET_BECAME_ACTIVE",reason_code="ACTIVE",transition_only=True)
                            worker_result = await self._run_worker_turn(assignment_id, view.profile)
                    if isinstance(worker_result, SupervisorResult):
                        return self._pause(
                            assignment_id,
                            worker_result.pause_reason or PauseReason.INVALID_STATE,
                            worker_result.detail or "worker turn was not admitted",
                            new_worker_turns,
                            new_reviewer_turns,
                            reviews,
                        )
                    execution = worker_result
                    new_worker_turns += 1
                    if execution.state == TurnExecutionState.UNCERTAIN:
                        return self._pause(assignment_id, PauseReason.RUNTIME_UNCERTAIN, execution.error or "worker outcome uncertain", new_worker_turns, new_reviewer_turns, reviews)
                    if execution.state == TurnExecutionState.KNOWN_FAILED and execution.error:
                        if execution.error == "TASKLEDGER_PROVIDER_TOKEN_LIMIT":
                            return self._pause(assignment_id, PauseReason.BUDGET_EXHAUSTED, "worker provider-turn token guard interrupted a runaway turn", new_worker_turns, new_reviewer_turns, reviews)
                        if execution.error.startswith("TASKLEDGER_"):
                            return self._pause(assignment_id, PauseReason.STALLED, f"worker loop guard interrupted the turn: {execution.error}", new_worker_turns, new_reviewer_turns, reviews)
            except ProviderAdmissionStopped:
                return self._pause(assignment_id, PauseReason.USER_INTERRUPTED, "pause requested before provider dispatch",
                    new_worker_turns, new_reviewer_turns, reviews)
            except Exception as exc:
                if self.manage_run:
                    self.journal.finish_run(self.run_id, "FAILED", reason=PauseReason.INVALID_STATE.value, detail=str(exc))
                raise

    async def _worker_session(self, assignment_id: str, profile: str) -> AgentSession:
        existing = self.journal.find_active_session(project_id=self.project_id, role=SessionRole.WORKER.value, subject_id=assignment_id)
        cwd = self.ledger.worker_cwd(assignment_id)
        identity = self.runtime.session_identity(role=SessionRole.WORKER, profile=profile, subject_id=assignment_id, cwd=cwd, writable=True)
        runtime_identity = identity.as_dict()
        config_hash = sha256(canonical(runtime_identity))
        if existing:
            if existing.config_hash != config_hash or existing.runtime_identity != runtime_identity:
                raise ConfigurationDrift("persisted worker runtime configuration differs from the resolved profile")
            if self.stop_requested(): raise ProviderAdmissionStopped("pause requested before provider session resume")
            await self.runtime.resume_session(
                thread_id=existing.thread_id, role=SessionRole.WORKER, profile=profile,
                subject_id=assignment_id, cwd=cwd, writable=True,
                cumulative_usage_baseline=self.journal.latest_cumulative_usage(existing.id),
            )
            return existing
        if self.stop_requested(): raise ProviderAdmissionStopped("pause requested before provider session start")
        runtime_session = await self.runtime.start_session(role=SessionRole.WORKER, profile=profile, subject_id=assignment_id, cwd=cwd, writable=True)
        if runtime_session.identity and runtime_session.identity != identity:
            raise ConfigurationDrift("runtime started a worker with an unexpected semantic configuration")
        return self.journal.create_session(run_id=self.run_id, project_id=self.project_id, role=SessionRole.WORKER.value, profile=profile, subject_id=assignment_id, external_thread_id=runtime_session.thread_id, config_hash=config_hash, runtime_identity=runtime_identity)

    async def _run_worker_turn(self, assignment_id: str, profile: str) -> TurnExecution | SupervisorResult:
        try:
            worker = await self._worker_session(assignment_id, profile)
        except ConfigurationDrift as exc:
            return SupervisorResult(SupervisorStatus.PAUSED, assignment_id, PauseReason.CONFIGURATION_DRIFT, str(exc))
        worker_limit = self.config.max_worker_turns + self.journal.granted_amount(
            run_id=self.run_id, kind="WORKER_TURNS"
        )
        if self.journal.session_turn_count(worker.id) >= worker_limit:
            return SupervisorResult(SupervisorStatus.PAUSED, assignment_id, PauseReason.MAX_TURNS, "worker turn budget exhausted")
        if self.journal.consecutive_stalled_turns(worker.id) >= self.config.max_consecutive_stalled_turns:
            return SupervisorResult(SupervisorStatus.PAUSED, assignment_id, PauseReason.STALLED, "worker made no durable progress")
        if self.journal.consecutive_failures(worker.id) >= self.config.max_consecutive_runtime_failures:
            return SupervisorResult(SupervisorStatus.PAUSED, assignment_id, PauseReason.RUNTIME_FAILED, "worker runtime failure budget exhausted")
        if self._token_budget_exhausted(self.worker_token_reserve) or self._elapsed_budget_exhausted():
            return SupervisorResult(SupervisorStatus.PAUSED, assignment_id, PauseReason.BUDGET_EXHAUSTED, "controller admission budget exhausted")
        if self._run_worker_budget_exhausted():
            return SupervisorResult(SupervisorStatus.PAUSED, assignment_id, PauseReason.BUDGET_EXHAUSTED, "project worker turn budget exhausted")
        first_turn = self.journal.session_turn_count(worker.id) == 0
        if hasattr(self.ledger, "worker_prompt_packet"):
            packet = self.ledger.worker_prompt_packet(
                assignment_id, first_turn=first_turn,
                previous_hashes=self.journal.latest_context_hashes(worker.id),
                include_policy_update=(
                    not first_turn and self.journal.worker_prompt_policy_version(worker.id) < 2
                ),
            )
        else:
            text = self.ledger.worker_prompt(assignment_id, first_turn=first_turn)
            packet = PromptPacket(
                text=text,
                dispatch_reason=DispatchReason.INITIAL_WORK if first_turn else DispatchReason.ACTIVE_CONTINUATION,
                controller_payload_bytes=len(text.encode("utf-8")),
            )
        return await self._run_turn(
            worker,
            prompt=packet.text,
            prompt_kind="worker",
            progress_before=self.ledger.progress_fingerprint(assignment_id),
            packet=packet,
        )

    async def _review(self, preparation: ReviewPreparation, *, review_kind: str = "submission") -> tuple[ReviewVerdict | None, str | None, int, TurnExecutionState | None]:
        if self.reviewer_capacity is None:
            return await self._review_with_capacity(preparation, review_kind=review_kind)
        if self.reviewer_capacity.locked():
            self.journal.append_event(run_id=self.run_id,scope_type="REVIEW",scope_id=preparation.submission_id,event_type="TARGET_WAITING_FOR_REVIEWER_CAPACITY",reason_code="WAITING_FOR_REVIEWER_CAPACITY",transition_only=True)
        async with self.reviewer_capacity:
            self.journal.append_event(run_id=self.run_id,scope_type="REVIEW",scope_id=preparation.submission_id,event_type="TARGET_BECAME_ACTIVE",reason_code="ACTIVE",transition_only=True)
            return await self._review_with_capacity(preparation, review_kind=review_kind)

    async def _review_with_capacity(self, preparation: ReviewPreparation, *, review_kind: str) -> tuple[ReviewVerdict | None, str | None, int, TurnExecutionState | None]:
        subject = preparation.submission_id
        session = self.journal.find_active_session(project_id=self.project_id, role=SessionRole.REVIEWER.value, subject_id=subject)
        cwd = self.ledger.checkpoint_reviewer_cwd(subject) if review_kind == "checkpoint" else self.ledger.reviewer_cwd(subject)
        identity = self.runtime.session_identity(role=SessionRole.REVIEWER, profile=self.config.reviewer_profile, subject_id=subject, cwd=cwd, writable=False)
        runtime_identity = identity.as_dict()
        config_hash = sha256(canonical(runtime_identity))
        if session is None:
            if self.stop_requested(): raise ProviderAdmissionStopped("pause requested before provider session start")
            runtime_session = await self.runtime.start_session(role=SessionRole.REVIEWER, profile=self.config.reviewer_profile, subject_id=subject, cwd=cwd, writable=False)
            if runtime_session.identity and runtime_session.identity != identity:
                raise ConfigurationDrift("runtime started a reviewer with an unexpected semantic configuration")
            session = self.journal.create_session(run_id=self.run_id, project_id=self.project_id, role=SessionRole.REVIEWER.value, profile=self.config.reviewer_profile, subject_id=subject, external_thread_id=runtime_session.thread_id, config_hash=config_hash, runtime_identity=runtime_identity)
        elif session.config_hash != config_hash or session.runtime_identity != runtime_identity:
            raise ConfigurationDrift("persisted reviewer runtime configuration differs from the resolved profile")
        else:
            if self.stop_requested(): raise ProviderAdmissionStopped("pause requested before provider session resume")
            await self.runtime.resume_session(
                thread_id=session.thread_id, role=SessionRole.REVIEWER,
                profile=self.config.reviewer_profile, subject_id=subject, cwd=cwd, writable=False,
                cumulative_usage_baseline=self.journal.latest_cumulative_usage(session.id),
            )

        started = 0
        reviewer_limit = self.config.max_reviewer_turns_per_submission + self.journal.granted_amount(run_id=self.run_id, kind="REVIEWER_TURNS")
        original_prompt = self.ledger.checkpoint_reviewer_prompt(preparation) if review_kind == "checkpoint" else self.ledger.reviewer_prompt(preparation)
        schema = self.ledger.checkpoint_reviewer_output_schema(preparation) if review_kind == "checkpoint" else self.ledger.reviewer_output_schema(preparation)
        prompt_kind = "checkpoint-reviewer" if review_kind == "checkpoint" else "reviewer"
        while self.journal.session_turn_count(session.id) < reviewer_limit:
            terminal = self.journal.terminal_unconsumed_turn(session.id)
            if terminal is None:
                if self._token_budget_exhausted() or self._run_reviewer_budget_exhausted():
                    return None, None, started, TurnExecutionState.KNOWN_FAILED
                retry = self.journal.session_completed_turn_count(session.id) > 0
                prompt = (
                    "Your previous result did not satisfy the required output schema. Return a corrected structured result only. Do not redo the review unless necessary."
                    if retry else original_prompt
                )
                reason = DispatchReason.STRUCTURED_OUTPUT_RETRY if retry else (
                    DispatchReason.CHECKPOINT_REVIEW if review_kind == "checkpoint" else DispatchReason.SUBMISSION_REVIEW
                )
                packet = PromptPacket(
                    text=prompt, dispatch_reason=reason,
                    controller_payload_bytes=len(prompt.encode("utf-8")),
                    output_schema_bytes=len(canonical(schema).encode("utf-8")),
                )
                execution = await self._run_turn(session, prompt=prompt, prompt_kind=prompt_kind, output_schema=schema, packet=packet)
                started += 1
                if execution.state == TurnExecutionState.UNCERTAIN:
                    return None, execution.local_turn_id, started, execution.state
                terminal = self.journal.terminal_unconsumed_turn(session.id)
            if terminal is None:
                continue
            if terminal.state == "FAILED":
                self.journal.consume_turn(terminal.id)
                continue
            verdict = parse_verdict(terminal.result.structured_output if terminal.result else None, expected_criterion_ids=preparation.expected_criterion_ids, acceptance_allowed=preparation.acceptance_allowed)
            if verdict is not None:
                return verdict, terminal.id, started, None
            self.journal.consume_turn(terminal.id)
        return None, None, started, TurnExecutionState.KNOWN_FAILED

    def _close_reviewer(self, subject_id: str) -> None:
        reviewer = self.journal.find_active_session(
            project_id=self.project_id, role=SessionRole.REVIEWER.value, subject_id=subject_id
        )
        if reviewer:
            self.journal.close_session(reviewer.id)

    async def _run_turn(self, session: AgentSession, *, prompt: str, prompt_kind: str, progress_before: str | None = None, output_schema: dict[str, Any] | None = None, packet: PromptPacket | None = None) -> TurnExecution:
        if self.stop_requested():
            raise ProviderAdmissionStopped("pause requested before provider dispatch")
        local_id = self.journal.begin_turn(
            session.id, prompt_kind, progress_before=progress_before, packet=packet,
            output_schema_bytes=len(canonical(output_schema).encode("utf-8")) if output_schema is not None else 0,
        )
        try:
            if self.stop_requested():
                self.journal.fail_turn(local_id, "pause requested before provider dispatch", uncertain=False)
                self.journal.consume_turn(local_id)
                raise ProviderAdmissionStopped("pause requested before provider dispatch")
            handle = await self.runtime.start_turn(thread_id=session.thread_id, prompt=prompt, output_schema=output_schema)
        except ProviderAdmissionStopped:
            raise
        except Exception as exc:
            self.journal.fail_turn(local_id, f"dispatch outcome unknown: {exc}", uncertain=True)
            return TurnExecution(TurnExecutionState.UNCERTAIN, local_id, error=str(exc))
        self.journal.acknowledge_turn(local_id, handle)
        try:
            result = await asyncio.wait_for(self.runtime.wait_turn(handle), timeout=self.config.turn_timeout_seconds)
        except Exception as exc:
            inspection = await self.runtime.inspect_turn(handle)
            if inspection.state == "COMPLETED" and inspection.result:
                result = inspection.result
            elif inspection.state == "FAILED":
                self.journal.fail_turn(local_id, inspection.error or str(exc), uncertain=False, result=inspection.result)
                return TurnExecution(TurnExecutionState.KNOWN_FAILED, local_id, error=inspection.error or str(exc))
            else:
                self.journal.fail_turn(
                    local_id,
                    inspection.error or str(exc),
                    uncertain=True,
                    result=inspection.result,
                )
                return TurnExecution(TurnExecutionState.UNCERTAIN, local_id, error=inspection.error or str(exc))
        self.journal.record_usage_events(local_id, self.runtime.usage_events(result.handle))
        self.journal.complete_turn(local_id, result)
        return TurnExecution(TurnExecutionState.COMPLETED, local_id, result=result)

    def _terminal_result(self, assignment_id: str, view, worker_turns: int, reviewer_turns: int, reviews: int) -> SupervisorResult | None:
        if view.status == ExecutionStatus.COMPLETED:
            self._close_worker(assignment_id)
            if self.manage_run:
                self.journal.finish_run(self.run_id, "COMPLETED")
            return SupervisorResult(SupervisorStatus.INTEGRATED, assignment_id, worker_turns=worker_turns, reviewer_turns=reviewer_turns, submission_reviews=reviews, usage=self.journal.usage(run_id=self.run_id))
        mapping = {
            ExecutionStatus.BLOCKED: PauseReason.BLOCKED,
            ExecutionStatus.REVOKED: PauseReason.ESCALATION_REQUIRED,
            ExecutionStatus.ACCEPTED: PauseReason.INTEGRATION_FAILED,
            ExecutionStatus.INTEGRATION_UNCERTAIN: PauseReason.INTEGRATION_UNCERTAIN,
        }
        reason = mapping.get(view.status)
        if reason is not None and view.status != ExecutionStatus.BLOCKED:
            self._close_worker(assignment_id)
        return self._pause(assignment_id, reason, view.detail or view.status.value, worker_turns, reviewer_turns, reviews) if reason else None

    def _close_worker(self, assignment_id: str) -> None:
        worker = self.journal.find_active_session(
            project_id=self.project_id, role=SessionRole.WORKER.value, subject_id=assignment_id
        )
        if worker:
            self.journal.close_session(worker.id)

    def _token_budget_exhausted(self, reserve: int = 0) -> bool:
        limit = self.config.max_total_tokens
        if limit is None:
            return False
        if self.journal.missing_usage_count(run_id=self.run_id):
            return True
        limit += self.journal.granted_amount(run_id=self.run_id, kind="TOKENS")
        return self.journal.usage(run_id=self.run_id).total_tokens >= max(0, limit - reserve)

    def _run_worker_budget_exhausted(self) -> bool:
        if self.run_worker_turn_limit is None:
            return False
        limit = self.run_worker_turn_limit + self.journal.granted_amount(run_id=self.run_id, kind="WORKER_TURNS")
        return self.journal.run_turn_count(run_id=self.run_id, roles=(SessionRole.WORKER.value,)) >= limit

    def _run_reviewer_budget_exhausted(self) -> bool:
        if self.run_reviewer_turn_limit is None:
            return False
        limit = self.run_reviewer_turn_limit + self.journal.granted_amount(run_id=self.run_id, kind="REVIEWER_TURNS")
        roles = (SessionRole.REVIEWER.value, SessionRole.REQUIREMENT_REVIEWER.value)
        return self.journal.run_turn_count(run_id=self.run_id, roles=roles) >= limit

    def _elapsed_budget_exhausted(self) -> bool:
        limit = self.config.max_elapsed_seconds + self.journal.granted_amount(run_id=self.run_id, kind="ELAPSED_SECONDS")
        return self.journal.elapsed_seconds(run_id=self.run_id) >= limit

    def _pause(self, assignment_id: str, reason: PauseReason, detail: str, worker_turns: int = 0, reviewer_turns: int = 0, reviews: int = 0) -> SupervisorResult:
        if self.manage_run:
            self.journal.finish_run(self.run_id, "PAUSED", reason=reason.value, detail=detail)
        return SupervisorResult(SupervisorStatus.PAUSED, assignment_id, reason, detail, worker_turns, reviewer_turns, reviews, self.journal.usage(run_id=self.run_id))


def parse_verdict(payload: dict[str, Any] | None, *, expected_criterion_ids: tuple[str, ...], acceptance_allowed: bool) -> ReviewVerdict | None:
    expected_fields = {"outcome", "criterion_results", "behavior_matches_intent", "required_evidence_present", "blocking_issues_remaining", "corrections", "notes", "blocker_id", "blocker"}
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        return None
    try:
        outcome = ReviewOutcome(payload["outcome"])
        criteria = payload["criterion_results"]
        if not isinstance(criteria, list):
            return None
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in criteria:
            if not isinstance(item, dict) or set(item) != {"criterion_id", "satisfied", "evidence"}:
                return None
            criterion_id, satisfied, evidence = item.get("criterion_id"), item.get("satisfied"), item.get("evidence")
            if not isinstance(criterion_id, str) or criterion_id in seen or not isinstance(satisfied, bool) or not isinstance(evidence, str) or not evidence:
                return None
            seen.add(criterion_id)
            normalized.append({"criterion_id": criterion_id, "satisfied": satisfied, "evidence": evidence})
        if seen != set(expected_criterion_ids) or len(normalized) != len(expected_criterion_ids):
            return None
        behavior, evidence_present, blocking = payload["behavior_matches_intent"], payload["required_evidence_present"], payload["blocking_issues_remaining"]
        if not all(isinstance(value, bool) for value in (behavior, evidence_present, blocking)):
            return None
        corrections, notes = payload.get("corrections"), payload.get("notes", "")
        if corrections is not None and not isinstance(corrections, str) or not isinstance(notes, str):
            return None
        all_satisfied = all(item["satisfied"] for item in normalized)
        if outcome == ReviewOutcome.ACCEPTED and (not acceptance_allowed or not all_satisfied or not behavior or not evidence_present or blocking):
            return None
        if outcome == ReviewOutcome.REJECTED and (not corrections or (all_satisfied and behavior and evidence_present and not blocking)):
            return None
        if outcome == ReviewOutcome.BLOCKED and not blocking:
            return None
        blocker_id = payload.get("blocker_id")
        blocker = payload.get("blocker")
        if blocker_id is not None and (not isinstance(blocker_id, str) or not blocker_id):
            return None
        if blocker is not None and (
            not isinstance(blocker, dict)
            or set(blocker) != {"category", "description"}
            or not all(isinstance(blocker.get(key), str) and blocker[key] for key in ("category", "description"))
        ):
            return None
        if outcome == ReviewOutcome.BLOCKED and bool(blocker_id) == bool(blocker):
            return None
        return ReviewVerdict(outcome, tuple(normalized), behavior, evidence_present, blocking, corrections, notes, blocker_id, blocker)
    except (KeyError, TypeError, ValueError):
        return None
