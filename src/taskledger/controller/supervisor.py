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
    SessionRole,
    SupervisorResult,
    SupervisorStatus,
    TurnExecution,
    TurnExecutionState,
    Usage,
)
from .ports import AgentRuntime, LedgerPort


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
    ) -> None:
        self.project_id = project_id
        self.run_id = run_id
        self.ledger = ledger
        self.runtime = runtime
        self.journal = journal
        self.config = config or SupervisorConfig()
        self.config.validate()

    async def reconcile_open_turns(self) -> list[str]:
        problems: list[str] = []
        for turn in self.journal.open_turns(project_id=self.project_id):
            if turn.state == "UNCERTAIN" or turn.external_turn_id is None:
                if turn.state != "UNCERTAIN":
                    self.journal.fail_turn(turn.id, "dispatch outcome unknown", uncertain=True)
                problems.append(turn.id)
                continue
            inspection = await self.runtime.inspect_turn(RuntimeTurnHandle(turn.thread_id, turn.external_turn_id))
            if inspection.state == "COMPLETED" and inspection.result is not None:
                self.journal.complete_turn(turn.id, inspection.result)
            elif inspection.state == "FAILED":
                self.journal.fail_turn(turn.id, inspection.error or "external turn failed", uncertain=False)
            elif inspection.state == "RUNNING":
                problems.append(turn.id)
            else:
                self.journal.fail_turn(turn.id, inspection.error or "external turn outcome unknown", uncertain=True)
                problems.append(turn.id)
        return problems

    def reconcile_resolved_reviewer_sessions(self) -> None:
        for session in self.journal.active_sessions(project_id=self.project_id, role=SessionRole.REVIEWER.value):
            if self.ledger.submission_pending(session.subject_id):
                continue
            turn = self.journal.terminal_unconsumed_turn(session.id)
            if turn is not None and turn.state in {"COMPLETED", "FAILED"}:
                self.journal.consume_turn(turn.id)
            self.journal.close_session(session.id)

    async def run_assignment(self, assignment_id: str) -> SupervisorResult:
        with self.journal.project_lock(self.project_id):
            new_worker_turns = 0
            new_reviewer_turns = 0
            reviews = 0
            try:
                recovery = await self.reconcile_open_turns()
                if recovery:
                    return self._pause(assignment_id, PauseReason.RUNTIME_UNCERTAIN, f"unresolved external turns: {', '.join(recovery)}")
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
                        verdict, turn_id, started, error = await self._review(preparation)
                        new_reviewer_turns += started
                        if error == TurnExecutionState.UNCERTAIN:
                            return self._pause(assignment_id, PauseReason.RUNTIME_UNCERTAIN, "reviewer outcome is uncertain", new_worker_turns, new_reviewer_turns, reviews)
                        if verdict is None or turn_id is None:
                            return self._pause(assignment_id, PauseReason.REVIEWER_FAILURE, "reviewer verdict budget exhausted", new_worker_turns, new_reviewer_turns, reviews)
                        try:
                            self.ledger.apply_review(preparation.submission_id, verdict)
                        except Exception as exc:
                            return self._pause(assignment_id, PauseReason.INVALID_STATE, f"ledger refused reviewer verdict: {exc}", new_worker_turns, new_reviewer_turns, reviews)
                        self.journal.consume_turn(turn_id)
                        reviewer = self.journal.find_active_session(project_id=self.project_id, role=SessionRole.REVIEWER.value, subject_id=preparation.submission_id)
                        if reviewer:
                            self.journal.close_session(reviewer.id)
                        reviews += 1
                        continue

                    if view.status != ExecutionStatus.ACTIVE:
                        return self._pause(assignment_id, PauseReason.INVALID_STATE, f"unexpected execution status {view.status.value}", new_worker_turns, new_reviewer_turns, reviews)

                    worker = await self._worker_session(assignment_id, view.profile)
                    if worker.profile != view.profile:
                        return self._pause(assignment_id, PauseReason.INVALID_STATE, "persisted worker profile changed", new_worker_turns, new_reviewer_turns, reviews)
                    worker_limit = self.config.max_worker_turns + self.journal.granted_amount(run_id=self.run_id, kind="WORKER_TURNS")
                    if self.journal.session_turn_count(worker.id) >= worker_limit:
                        return self._pause(assignment_id, PauseReason.MAX_TURNS, "worker turn budget exhausted", new_worker_turns, new_reviewer_turns, reviews)
                    if self._token_budget_exhausted() or self._elapsed_budget_exhausted():
                        return self._pause(assignment_id, PauseReason.BUDGET_EXHAUSTED, "controller admission budget exhausted", new_worker_turns, new_reviewer_turns, reviews)

                    execution = await self._run_turn(
                        worker,
                        prompt=self.ledger.worker_prompt(assignment_id, first_turn=self.journal.session_turn_count(worker.id) == 0),
                        prompt_kind="worker",
                        progress_before=self.ledger.progress_fingerprint(assignment_id),
                    )
                    new_worker_turns += 1
                    if execution.state == TurnExecutionState.UNCERTAIN:
                        return self._pause(assignment_id, PauseReason.RUNTIME_UNCERTAIN, execution.error or "worker outcome uncertain", new_worker_turns, new_reviewer_turns, reviews)
            except Exception as exc:
                self.journal.finish_run(self.run_id, "FAILED", reason=PauseReason.INVALID_STATE.value, detail=str(exc))
                raise

    async def _worker_session(self, assignment_id: str, profile: str) -> AgentSession:
        existing = self.journal.find_active_session(project_id=self.project_id, role=SessionRole.WORKER.value, subject_id=assignment_id)
        cwd = self.ledger.worker_cwd(assignment_id)
        config_hash = sha256(canonical({"profile": profile, "cwd": cwd, "writable": True}))
        if existing:
            if existing.config_hash != config_hash:
                return existing
            await self.runtime.resume_session(thread_id=existing.thread_id, role=SessionRole.WORKER, profile=profile, subject_id=assignment_id, cwd=cwd, writable=True)
            return existing
        runtime_session = await self.runtime.start_session(role=SessionRole.WORKER, profile=profile, subject_id=assignment_id, cwd=cwd, writable=True)
        return self.journal.create_session(run_id=self.run_id, project_id=self.project_id, role=SessionRole.WORKER.value, profile=profile, subject_id=assignment_id, external_thread_id=runtime_session.thread_id, config_hash=config_hash)

    async def _review(self, preparation: ReviewPreparation) -> tuple[ReviewVerdict | None, str | None, int, TurnExecutionState | None]:
        subject = preparation.submission_id
        session = self.journal.find_active_session(project_id=self.project_id, role=SessionRole.REVIEWER.value, subject_id=subject)
        cwd = self.ledger.reviewer_cwd(subject)
        config_hash = sha256(canonical({"profile": self.config.reviewer_profile, "cwd": cwd, "writable": False}))
        if session is None:
            runtime_session = await self.runtime.start_session(role=SessionRole.REVIEWER, profile=self.config.reviewer_profile, subject_id=subject, cwd=cwd, writable=False)
            session = self.journal.create_session(run_id=self.run_id, project_id=self.project_id, role=SessionRole.REVIEWER.value, profile=self.config.reviewer_profile, subject_id=subject, external_thread_id=runtime_session.thread_id, config_hash=config_hash)
        elif session.config_hash != config_hash:
            return None, None, 0, TurnExecutionState.KNOWN_FAILED
        else:
            await self.runtime.resume_session(thread_id=session.thread_id, role=SessionRole.REVIEWER, profile=self.config.reviewer_profile, subject_id=subject, cwd=cwd, writable=False)

        started = 0
        reviewer_limit = self.config.max_reviewer_turns_per_submission + self.journal.granted_amount(run_id=self.run_id, kind="REVIEWER_TURNS")
        while self.journal.session_turn_count(session.id) < reviewer_limit:
            terminal = self.journal.terminal_unconsumed_turn(session.id)
            if terminal is None:
                if self._token_budget_exhausted():
                    return None, None, started, TurnExecutionState.KNOWN_FAILED
                execution = await self._run_turn(session, prompt=self.ledger.reviewer_prompt(preparation), prompt_kind="reviewer", output_schema=self.ledger.reviewer_output_schema(preparation))
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

    async def _run_turn(self, session: AgentSession, *, prompt: str, prompt_kind: str, progress_before: str | None = None, output_schema: dict[str, Any] | None = None) -> TurnExecution:
        local_id = self.journal.begin_turn(session.id, prompt_kind, progress_before=progress_before)
        try:
            handle = await self.runtime.start_turn(thread_id=session.thread_id, prompt=prompt, output_schema=output_schema)
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
                self.journal.fail_turn(local_id, inspection.error or str(exc), uncertain=False)
                return TurnExecution(TurnExecutionState.KNOWN_FAILED, local_id, error=inspection.error or str(exc))
            else:
                self.journal.fail_turn(local_id, inspection.error or str(exc), uncertain=True)
                return TurnExecution(TurnExecutionState.UNCERTAIN, local_id, error=inspection.error or str(exc))
        self.journal.complete_turn(local_id, result)
        return TurnExecution(TurnExecutionState.COMPLETED, local_id, result=result)

    def _terminal_result(self, assignment_id: str, view, worker_turns: int, reviewer_turns: int, reviews: int) -> SupervisorResult | None:
        if view.status == ExecutionStatus.COMPLETED:
            self.journal.finish_run(self.run_id, "COMPLETED")
            return SupervisorResult(SupervisorStatus.INTEGRATED, assignment_id, worker_turns=worker_turns, reviewer_turns=reviewer_turns, submission_reviews=reviews, usage=self.journal.usage(run_id=self.run_id))
        mapping = {
            ExecutionStatus.BLOCKED: PauseReason.BLOCKED,
            ExecutionStatus.REVOKED: PauseReason.ESCALATION_REQUIRED,
            ExecutionStatus.ACCEPTED: PauseReason.INTEGRATION_FAILED,
            ExecutionStatus.INTEGRATION_UNCERTAIN: PauseReason.INTEGRATION_UNCERTAIN,
        }
        reason = mapping.get(view.status)
        return self._pause(assignment_id, reason, view.detail or view.status.value, worker_turns, reviewer_turns, reviews) if reason else None

    def _token_budget_exhausted(self) -> bool:
        limit = self.config.max_total_tokens
        if limit is None:
            return False
        limit += self.journal.granted_amount(run_id=self.run_id, kind="TOKENS")
        return self.journal.usage(run_id=self.run_id).total_tokens >= limit

    def _elapsed_budget_exhausted(self) -> bool:
        limit = self.config.max_elapsed_seconds + self.journal.granted_amount(run_id=self.run_id, kind="ELAPSED_SECONDS")
        return self.journal.elapsed_seconds(run_id=self.run_id) >= limit

    def _pause(self, assignment_id: str, reason: PauseReason, detail: str, worker_turns: int = 0, reviewer_turns: int = 0, reviews: int = 0) -> SupervisorResult:
        self.journal.finish_run(self.run_id, "PAUSED", reason=reason.value, detail=detail)
        return SupervisorResult(SupervisorStatus.PAUSED, assignment_id, reason, detail, worker_turns, reviewer_turns, reviews, self.journal.usage(run_id=self.run_id))


def parse_verdict(payload: dict[str, Any] | None, *, expected_criterion_ids: tuple[str, ...], acceptance_allowed: bool) -> ReviewVerdict | None:
    if not isinstance(payload, dict):
        return None
    try:
        outcome = ReviewOutcome(payload["outcome"])
        criteria = payload["criterion_results"]
        if not isinstance(criteria, list):
            return None
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in criteria:
            if not isinstance(item, dict):
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
        if outcome == ReviewOutcome.BLOCKED and bool(blocker_id) == bool(blocker):
            return None
        return ReviewVerdict(outcome, tuple(normalized), behavior, evidence_present, blocking, corrections, notes, blocker_id, blocker)
    except (KeyError, TypeError, ValueError):
        return None
