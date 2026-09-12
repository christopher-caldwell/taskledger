from __future__ import annotations

import contextlib
import asyncio
import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Callable

from taskledger import git
from taskledger.core import LedgerError, canonical, now, require_object, sha256, text
from taskledger.db import transaction

from .lifecycle import LifecycleOwner
from .mechanics import active_specification_revision, project_controller_config


@dataclass(frozen=True)
class AdmittedLifecycle:
    lifecycle_id: str
    run_id: str
    preparation_id: str | None
    owner: LifecycleOwner


async def _run_interruptible_planner(planner, *, stop_requested, journal, runtime, project):
    """Stop preparation at the same provider-reconciliation boundary as execution."""
    if stop_requested is None:
        return await planner.run()
    planning = asyncio.create_task(planner.run())
    async def wait_stop():
        while not stop_requested():
            await asyncio.sleep(.05)
    stopper = asyncio.create_task(wait_stop())
    done, _ = await asyncio.wait({planning, stopper}, return_when=asyncio.FIRST_COMPLETED)
    if planning in done:
        stopper.cancel()
        return await planning
    planning.cancel()
    await asyncio.gather(planning, return_exceptions=True)
    from taskledger.controller.model import RuntimeTurnHandle
    for turn in journal.open_turns(project_id=project["id"]):
        result = None
        if turn.external_turn_id:
            handle = RuntimeTurnHandle(turn.thread_id, turn.external_turn_id)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(runtime.interrupt_turn(handle), timeout=5)
            with contextlib.suppress(Exception):
                inspection = await asyncio.wait_for(runtime.inspect_turn(handle), timeout=1)
                result = inspection.result
                if inspection.state == "COMPLETED" and result is not None:
                    journal.complete_turn(turn.id, result)
                    journal.consume_turn(turn.id)
                    continue
                if inspection.state == "FAILED":
                    journal.fail_turn(turn.id, inspection.error or "INTERRUPTED", uncertain=False, result=result)
                    journal.consume_turn(turn.id)
                    continue
        journal.fail_turn(turn.id, "interrupted before a terminal provider result was proven", uncertain=True, result=result)
    raise LedgerError("PAUSE_REQUESTED", "Preparation paused safely.")


async def prepare_project(service, project, principal, data: dict[str, Any], *,
                          runtime_factory=None, stop_requested: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Run preparation on the caller-owned loop and caller-owned database session."""
    from taskledger.controller.app_server import AppServerRuntime
    from taskledger.controller.initial_planning import InitialPlanner, finish_preparation, parse_preparation_request, store_planning_preparation
    from taskledger.controller.journal import Journal
    from taskledger.controller.model import SessionRole

    spec_path, config, requested_group_id = parse_preparation_request(data)
    info = git.inspect(project["repository_root"])
    if not info["has_commits"]:
        raise LedgerError("INITIAL_COMMIT_REQUIRED", "The repository needs an initial commit before preparation.")
    if not git.clean(info["root"]):
        raise LedgerError("CANONICAL_WORKTREE_DIRTY", "The canonical worktree must be clean before preparation.")
    journal = Journal(service.con, service.home)
    lease = journal.project_lock(project["id"])
    lease.__enter__()
    try:
        attempt = service.begin_preparation_attempt(project, requested_group_id)
    except BaseException:
        lease.__exit__(None, None, None)
        raise
    runtime = None
    try:
        if project["canonical_branch"] != info["branch"]:
            raise LedgerError("CANONICAL_BRANCH_NOT_CHECKED_OUT", "Preparation requires the canonical branch to be checked out.")
        active = service.con.execute("SELECT id FROM controller_runs WHERE project_id=? AND state='RUNNING'", (project["id"],)).fetchone()
        if active:
            raise LedgerError("INVALID_REQUEST", "A controller run is already active.", details={"run_id": active["id"]})
        existing = service.con.execute("SELECT id FROM project_preparations WHERE project_id=? AND state IN ('PLANNING','AWAITING_APPROVAL')", (project["id"],)).fetchone()
        if existing:
            raise LedgerError("STALE_STATE", "An open preparation already exists.", details={"preparation_id": existing["id"]})
        spec = service.con.execute("SELECT id FROM specifications WHERE project_id=? AND relative_path=? AND lifecycle='ACTIVE'", (project["id"], spec_path)).fetchone()
        if spec is None:
            service.register_spec(project, principal, {"relative_path": spec_path})
        revision = active_specification_revision(service, project, relative_path=spec_path)
        diagnostics = service.preparation_diagnostics(project, {"profiles": ["routine", "complex"], "local_inputs": config.local_inputs,
            "services": config.services, "host_agent_availability": {"routine": True, "complex": True}})
        if not diagnostics["ready_for_local_preparation"]:
            raise LedgerError("INVALID_REQUEST", "Preparation preflight failed.", details={"preflight": diagnostics})
        runtime = (runtime_factory or AppServerRuntime)(repository_root=project["repository_root"], worker_tools={})
        identities = {
            "task_creator": runtime.session_identity(role=SessionRole.TASK_CREATOR, profile="taskledger_task_creator", subject_id="preflight", cwd=project["repository_root"], writable=False).as_dict(),
            "routine": runtime.session_identity(role=SessionRole.WORKER, profile="routine", subject_id="preflight", cwd=project["repository_root"], writable=True).as_dict(),
            "complex": runtime.session_identity(role=SessionRole.WORKER, profile="complex", subject_id="preflight", cwd=project["repository_root"], writable=True).as_dict(),
            "reviewer": runtime.session_identity(role=SessionRole.REVIEWER, profile="taskledger_reviewer", subject_id="preflight", cwd=project["repository_root"], writable=False).as_dict(),
        }
        manifest = {"canonical_starting_oid": info["head_oid"], "specification_hash": revision["content_hash"],
            "specification_revision_id": revision["revision_id"], "scope": "PREPARATION",
            "controller_configuration_hash": sha256(canonical(config.as_dict())),
            "taskledger_schema_version": service.con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
            "codex_protocol_identity": runtime.protocol_identity}
        run_id = journal.create_run(project["id"], mode="PREPARATION", config={"limits": config.as_dict(), "manifest": manifest})
        service.link_preparation_attempt(attempt["attempt_id"], planning_run_id=run_id)
        preparation_id = store_planning_preparation(service, project, run_id=run_id, starting_oid=info["head_oid"],
            spec_id=revision["specification_id"], spec_hash=revision["content_hash"], profile_hashes=identities, config=config)
        service.link_preparation_attempt(attempt["attempt_id"], preparation_id=preparation_id)
        try:
            planner = InitialPlanner(service=service, project=project, journal=journal, runtime=runtime,
                run_id=run_id, preparation_id=preparation_id, spec_path=spec_path,
                specification_bytes=revision["content_bytes"], specification_hash=revision["content_hash"], config=config,
                stop_requested=stop_requested)
            proposal, turn_id = await _run_interruptible_planner(planner, stop_requested=stop_requested,
                journal=journal, runtime=runtime, project=project)
            current = active_specification_revision(service, project, specification_id=revision["specification_id"])
            if current["revision_id"] != revision["revision_id"] or current["content_hash"] != revision["content_hash"]:
                raise LedgerError("PREPARATION_STALE", "Specification changed during preparation.")
            result = finish_preparation(service, preparation_id, proposal, turn_id)
            journal.finish_run(run_id, "COMPLETED" if result["status"] == "AWAITING_APPROVAL" else "FAILED",
                reason=None if result["status"] == "AWAITING_APPROVAL" else "AMBIGUOUS_REQUIREMENT")
        except BaseException as exc:
            if (journal.run(run_id) or {}).get("state") == "RUNNING":
                journal.finish_run(run_id, "FAILED", reason="INVALID_STATE", detail=str(exc))
            with contextlib.suppress(Exception):
                with transaction(service.con):
                    service.con.execute("UPDATE project_preparations SET state='FAILED',failure_reason=?,updated_at=? WHERE id=? AND state='PLANNING'", (str(exc), now(), preparation_id))
            raise
        success = result["status"] == "AWAITING_APPROVAL"
        service.finish_preparation_attempt(attempt["attempt_id"], succeeded=success,
            failure_reason=None if success else "AMBIGUOUS_REQUIREMENT")
        return {**result, "planning_run_id": run_id, "starting_oid": info["head_oid"], "canonical_branch": info["branch"],
            "profiles": identities, "preparation_attempt_id": attempt["attempt_id"], "run_group_id": attempt["run_group_id"]}
    except BaseException as exc:
        service.finish_preparation_attempt(attempt["attempt_id"], succeeded=False,
            failure_reason=exc.code if isinstance(exc, LedgerError) else exc.__class__.__name__)
        raise
    finally:
        if runtime is not None:
            await runtime.close()
        lease.__exit__(None, None, None)


def admit_prepared_start(service, project, principal, data: dict[str, Any], *, runtime_factory=None) -> AdmittedLifecycle:
    """Validate and durably admit an exact prepared run while holding its lease."""
    from taskledger.controller.app_server import AppServerRuntime
    from taskledger.controller.initial_planning import materialized_plan, preparation_row, proposal_sources_are_normalized
    from taskledger.controller.journal import Journal
    from taskledger.controller.model import SessionRole
    from taskledger.controller.project import validate_execution_policy

    require_object(data, {"preparation_id", "approve_proposal_hash", "live"}, {"preparation_id", "approve_proposal_hash", "live"})
    if data["live"] is not True:
        raise LedgerError("INVALID_REQUEST", "Live project execution requires live=true explicit opt in.")
    preparation = preparation_row(service, text(data["preparation_id"], "preparation_id"))
    if preparation["project_id"] != project["id"] or preparation["state"] != "AWAITING_APPROVAL":
        raise LedgerError("PREPARATION_STALE", "Preparation is not awaiting approval.")
    if text(data["approve_proposal_hash"], "approve_proposal_hash") != preparation["proposal_hash"]:
        raise LedgerError("PREPARATION_STALE", "Approved proposal hash does not match the immutable preparation.")
    if "sha256:" + sha256(canonical(preparation["proposal"])) != preparation["proposal_hash"]:
        raise LedgerError("PREPARATION_STALE", "Stored proposal no longer matches its immutable fingerprint.")
    if not proposal_sources_are_normalized(preparation["proposal"]):
        raise LedgerError("PREPARATION_INVALID", "Prepared proposal uses a legacy duplicate source shape; prepare a new proposal.")
    revision = active_specification_revision(service, project, specification_id=preparation["specification_id"])
    if revision["content_hash"] != preparation["specification_hash"]:
        raise LedgerError("PREPARATION_STALE", "Specification changed after preparation.")
    info = git.inspect(project["repository_root"])
    if info["branch"] != preparation["canonical_branch"] or info["head_oid"] != preparation["starting_oid"] or not git.clean(info["root"]):
        raise LedgerError("PREPARATION_STALE", "Repository branch, commit, or cleanliness changed after preparation.")
    stored = preparation["run_configuration"]
    diagnostics = service.preparation_diagnostics(project, {"profiles": ["routine", "complex"],
        "local_inputs": stored["preflight"]["local_inputs"], "services": stored["preflight"]["services"],
        "host_agent_availability": {"routine": True, "complex": True}})
    if not diagnostics["ready_for_local_preparation"]:
        raise LedgerError("PREPARATION_STALE", "Preflight no longer passes.", details={"preflight": diagnostics})
    runtime = (runtime_factory or AppServerRuntime)(repository_root=project["repository_root"], worker_tools={})
    identities = {
        "task_creator": runtime.session_identity(role=SessionRole.TASK_CREATOR, profile="taskledger_task_creator", subject_id="preflight", cwd=project["repository_root"], writable=False).as_dict(),
        "routine": runtime.session_identity(role=SessionRole.WORKER, profile="routine", subject_id="preflight", cwd=project["repository_root"], writable=True).as_dict(),
        "complex": runtime.session_identity(role=SessionRole.WORKER, profile="complex", subject_id="preflight", cwd=project["repository_root"], writable=True).as_dict(),
        "reviewer": runtime.session_identity(role=SessionRole.REVIEWER, profile="taskledger_reviewer", subject_id="preflight", cwd=project["repository_root"], writable=False).as_dict(),
    }
    if identities != preparation["profile_hashes"] or sha256(canonical(stored)) != preparation["run_configuration_hash"]:
        raise LedgerError("PREPARATION_STALE", "Resolved profiles or run configuration changed after preparation.")
    journal = Journal(service.con, service.home)
    lease = journal.project_lock(project["id"])
    lease.__enter__()
    try:
        active = service.con.execute("SELECT id FROM controller_runs WHERE project_id=? AND state='RUNNING'", (project["id"],)).fetchone()
        if active:
            raise LedgerError("PREPARATION_STALE", "Another controller run became active after preparation.", details={"run_id": active["id"]})
        applied = service.apply_plan(project, principal, materialized_plan(preparation))
        validated = service.validate_plan(project, principal)
        if not validated["valid"]:
            raise LedgerError("PLAN_VALIDATION_FAILED", "Prepared plan did not pass Taskledger validation.", details={"diagnostics": validated["diagnostics"]})
        raw_targets = [{"task_id": applied["task_ids"][entry["task_ref"]], "wave": entry["wave"],
            "worker_profile": entry["worker_profile"], "parallel_safe": entry["parallel_safe"], "write_surfaces": entry["write_surfaces"]}
            for entry in preparation["proposal"]["execution_policy"]]
        targets = validate_execution_policy(service, project, raw_targets)
        config = project_controller_config(stored["execution_limits"])
        run_group_id, attempts = service.preparation_attempts_for_preparation(preparation["id"])
        manifest = {"canonical_starting_oid": info["head_oid"], "starting_plan_fingerprint": validated["fingerprint"],
            "execution_policy_hash": hashlib.sha256(canonical(targets).encode()).hexdigest(),
            "controller_configuration_hash": hashlib.sha256(canonical(config.as_dict()).encode()).hexdigest(),
            "taskledger_schema_version": service.con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
            "codex_protocol_identity": runtime.protocol_identity, "scope": "POST_APPROVAL_EXECUTION",
            "preparation_run_id": preparation["planning_run_id"], "proposal_hash": preparation["proposal_hash"]}
        if run_group_id:
            manifest.update(preparation_run_group_id=run_group_id, preparation_attempt_ids=[item["id"] for item in attempts])
        run_id = journal.create_project_run(project["id"], config={"limits": config.as_dict(), "manifest": manifest}, targets=targets)
        with transaction(service.con):
            service.con.execute("UPDATE project_preparations SET state='APPROVED',execution_run_id=?,approved_at=?,updated_at=? WHERE id=? AND state='AWAITING_APPROVAL'",
                (run_id, now(), now(), preparation["id"]))
        owner = LifecycleOwner(service, project, principal, run_id, config, runtime, owned_lease=lease)
        return AdmittedLifecycle(run_id, run_id, preparation["id"], owner)
    except BaseException:
        lease.__exit__(None, None, None)
        raise


def admit_resume(service, project, principal, data: dict[str, Any], *, runtime_factory=None) -> AdmittedLifecycle:
    """Reconstruct and admit an existing project lifecycle without redispatching open turns."""
    from taskledger.controller.app_server import AppServerRuntime
    from taskledger.controller.journal import Journal
    from taskledger.controller.model import SessionRole

    require_object(data, {"run_id", "live"}, {"run_id", "live"})
    if data["live"] is not True:
        raise LedgerError("INVALID_REQUEST", "Live controller execution requires live=true explicit opt in.")
    journal = Journal(service.con, service.home)
    requested_id = text(data["run_id"], "run_id")
    existing = journal.run(requested_id)
    if not existing or existing["project_id"] != project["id"]:
        raise LedgerError("INVALID_REQUEST", "Controller run does not belong to this project.")
    if existing["mode"] == "ASSIGNMENT":
        from taskledger.controller.supervisor import Supervisor, SupervisorConfig
        from taskledger.controller.taskledger_adapter import TaskledgerLedgerAdapter
        from taskledger.controller.worker_broker import WorkerBroker, broker_socket_path
        from taskledger.controller.model import PauseReason, SupervisorResult, SupervisorStatus
        assignment_id = existing["config"]["assignment_id"]
        config = SupervisorConfig(**existing["config"]["limits"]); config.validate()
        assignment = service.con.execute("SELECT * FROM assignments WHERE id=? AND project_id=?", (assignment_id, project["id"])).fetchone()
        if not assignment:
            raise LedgerError("ASSIGNMENT_NOT_ACTIVE", "Assignment was not found.")
        token_path = service.home / "projects" / project["id"] / "assignments" / assignment_id / "worker-token"
        try: worker = service.authenticate(project["id"], token_path.read_text().strip(), "WORKER")
        except OSError as exc: raise LedgerError("ASSIGNMENT_NOT_ACTIVE", "Active assignment worker credential is unavailable.") from exc
        broker = WorkerBroker(service, worker, broker_socket_path(service.home, assignment_id))
        runtime = (runtime_factory or AppServerRuntime)(repository_root=project["repository_root"], worker_tools={assignment_id: broker.dispatch})
        runtime.session_identity(role=SessionRole.WORKER, profile=assignment["worker_profile"], subject_id=assignment_id,
            cwd=assignment["worktree_path"], writable=True)
        lease = journal.project_lock(project["id"]); lease.__enter__()
        try:
            run = journal.resume_run(requested_id)
            adapter = TaskledgerLedgerAdapter(service, project, principal, worker_tool_enabled=True)
            async def runner(stop_requested):
                return await Supervisor(project_id=project["id"], run_id=run["id"], ledger=adapter, runtime=runtime,
                    journal=journal, config=config, stop_requested=stop_requested, manage_run=True,
                    owns_project_lease=False).run_assignment(assignment_id)
            def paused(_reason, usage):
                return SupervisorResult(SupervisorStatus.PAUSED, assignment_id, PauseReason.USER_INTERRUPTED,
                    "foreground execution interrupted", usage=usage)
            owner = LifecycleOwner(service, project, principal, run["id"], config, runtime, owned_lease=lease,
                runner_factory=runner, pause_result_factory=paused, cleanup=broker.close)
            return AdmittedLifecycle(run["id"], run["id"], None, owner)
        except BaseException:
            lease.__exit__(None, None, None)
            raise
    if existing["mode"] != "PROJECT":
        raise LedgerError("INVALID_REQUEST", "Unsupported controller run mode.")
    config = project_controller_config(existing["config"]["limits"])
    runtime = (runtime_factory or AppServerRuntime)(repository_root=project["repository_root"], worker_tools={})
    try:
        for target in journal.targets(requested_id):
            if target["state"] not in {"INTEGRATED", "CANCELLED"}:
                runtime.session_identity(role=SessionRole.WORKER, profile=target["worker_profile"],
                    subject_id="preflight", cwd=project["repository_root"], writable=True)
        runtime.session_identity(role=SessionRole.REVIEWER, profile=config.supervisor.reviewer_profile,
            subject_id="preflight", cwd=project["repository_root"], writable=False)
        runtime.session_identity(role=SessionRole.TASK_CREATOR, profile=config.task_creator_profile,
            subject_id="preflight", cwd=project["repository_root"], writable=False)
        lease = journal.project_lock(project["id"])
        lease.__enter__()
        try:
            run = journal.resume_run(requested_id)
            owner = LifecycleOwner(service, project, principal, run["id"], config, runtime, owned_lease=lease)
            return AdmittedLifecycle(run["id"], run["id"], None, owner)
        except BaseException:
            lease.__exit__(None, None, None)
            raise
    except BaseException:
        # No await is available during admission; AppServerRuntime.close currently
        # releases asynchronous protocol resources after ownership transfers.
        raise
