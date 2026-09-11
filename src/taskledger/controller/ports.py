from __future__ import annotations

from typing import Any, Protocol

from .model import (
    ExecutionView,
    ReviewPreparation,
    ReviewVerdict,
    RuntimeSession,
    RuntimeIdentity,
    RuntimeTurnHandle,
    RuntimeTurnInspection,
    RuntimeTurnResult,
    SessionRole,
)


class AgentRuntime(Protocol):
    def session_identity(
        self, *, role: SessionRole, profile: str, subject_id: str, cwd: str | None, writable: bool
    ) -> RuntimeIdentity: ...

    async def start_session(
        self,
        *,
        role: SessionRole,
        profile: str,
        subject_id: str,
        cwd: str | None,
        writable: bool,
    ) -> RuntimeSession: ...

    async def resume_session(
        self, *, thread_id: str, role: SessionRole, profile: str, subject_id: str, cwd: str | None, writable: bool
    ) -> RuntimeSession: ...

    async def start_turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        output_schema: dict[str, Any] | None = None,
    ) -> RuntimeTurnHandle: ...

    async def wait_turn(self, handle: RuntimeTurnHandle) -> RuntimeTurnResult: ...

    async def inspect_turn(self, handle: RuntimeTurnHandle) -> RuntimeTurnInspection: ...

    def usage_events(self, handle: RuntimeTurnHandle) -> tuple[dict[str, Any], ...]: ...


class LedgerPort(Protocol):
    def execution_view(self, assignment_id: str) -> ExecutionView: ...

    def progress_fingerprint(self, assignment_id: str) -> str: ...

    def worker_prompt(self, assignment_id: str, *, first_turn: bool) -> str: ...

    def worker_cwd(self, assignment_id: str) -> str: ...

    def submission_pending(self, submission_id: str) -> bool: ...

    def checkpoint_pending(self, checkpoint_id: str) -> bool: ...

    def prepare_review(self, submission_id: str) -> ReviewPreparation: ...

    def prepare_checkpoint_review(self, checkpoint_id: str) -> ReviewPreparation: ...

    def reviewer_prompt(self, preparation: ReviewPreparation) -> str: ...

    def checkpoint_reviewer_prompt(self, preparation: ReviewPreparation) -> str: ...

    def reviewer_cwd(self, submission_id: str) -> str: ...

    def checkpoint_reviewer_cwd(self, checkpoint_id: str) -> str: ...

    def reviewer_output_schema(self, preparation: ReviewPreparation) -> dict[str, Any]: ...

    def checkpoint_reviewer_output_schema(self, preparation: ReviewPreparation) -> dict[str, Any]: ...

    def apply_review(self, submission_id: str, verdict: ReviewVerdict) -> ExecutionView: ...

    def apply_checkpoint_review(self, checkpoint_id: str, verdict: ReviewVerdict) -> ExecutionView: ...
