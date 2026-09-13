from __future__ import annotations

import json
import os
from importlib import resources
from pathlib import Path
from typing import Any, Callable

from . import git
from .core import MAX_SPEC, LedgerError, canonical, new_id, now, sha256
from .db import connect, transaction
from .repository import home_for_repository
from .service import Service


PROFILE_FILES = (
    "taskledger-task-creator.toml",
    "taskledger-reviewer.toml",
    "taskledger-worker-routine.toml",
    "taskledger-worker-complex.toml",
)
APPROVAL_FILE = "operator-approval.json"


def _yes(answer: str) -> bool:
    return answer.strip().lower() in {"y", "yes"}


def _ensure_ignore(root: Path) -> bool:
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if any(line.strip() == ".taskledger/" for line in existing.splitlines()):
        return False
    separator = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(existing + separator + ".taskledger/\n", encoding="utf-8")
    return True


def _install_profiles(root: Path) -> tuple[list[str], list[str]]:
    target = root / ".codex" / "agents"
    added, existing = [], []
    packaged = resources.files("taskledger.assets")
    for name in PROFILE_FILES:
        relative = f".codex/agents/{name}"
        destination = target / name
        if destination.exists():
            existing.append(relative)
            continue
        target.mkdir(parents=True, exist_ok=True)
        destination.write_text(packaged.joinpath(name).read_text(encoding="utf-8"), encoding="utf-8")
        added.append(relative)
    return added, existing


def bootstrap(repository: str, *, confirm: Callable[[str], str] = input,
              confirm_branch: str | None = None, commit_setup: bool = False) -> dict[str, Any]:
    info = git.inspect(repository)
    root = Path(str(info["root"]))
    home = home_for_repository(info, create=False)
    initialized = (home / "taskledger.sqlite3").is_file()
    if commit_setup and not git.clean(root):
        raise LedgerError("CANONICAL_WORKTREE_DIRTY", "Model bootstrap requires a clean repository before it changes setup files.")
    if confirm_branch is not None and confirm_branch != info["branch"]:
        raise LedgerError("BRANCH_CONFIRMATION_MISMATCH", "Confirmation must exactly match the detected branch.", details={"detected_branch": info["branch"]})
    if not initialized:
        if confirm_branch is None:
            prompt = f"Use '{info['branch']}' as the Taskledger canonical branch? [y/N] "
            try:
                answer = confirm(prompt)
            except EOFError:
                answer = ""
            if not _yes(answer):
                return {"status": "CANCELLED", "canonical_branch": info["branch"], "added": [], "existing": []}

    ignore_added = _ensure_ignore(root)
    added, existing = _install_profiles(root)
    if ignore_added:
        added.insert(0, ".taskledger/ to .gitignore")
    else:
        existing.insert(0, ".taskledger/ in .gitignore")

    setup_commit_oid = None
    if commit_setup and added:
        paths = [".gitignore", *(item for item in added if item.startswith(".codex/"))]
        result = git.run(root, ["add", "--", *paths], check=False)
        if result.returncode:
            raise LedgerError("GIT_COMMAND_FAILED", "Taskledger could not stage its setup files.", details={"stderr": result.stderr[-2000:]})
        result = git.run(root, ["commit", "-m", "Configure Taskledger", "--", *paths], check=False)
        if result.returncode:
            raise LedgerError("GIT_COMMAND_FAILED", "Taskledger could not commit its setup files.", details={"stderr": result.stderr[-2000:]})
        setup_commit_oid = git.oid(root)
        info = git.inspect(root)

    home = home_for_repository(info, create=True)
    service = Service(connect(home), home)
    try:
        initialized_result = service.init(str(root), str(info["branch"]) if not initialized else None)
    finally:
        service.con.close()
    return {
        "status": "COMPLETE",
        "canonical_branch": initialized_result["canonical_branch"],
        "added": added,
        "existing": existing,
        "repository_configuration_changed": bool(ignore_added or any(item.startswith(".codex/") for item in added)),
        "initialized": not initialized,
        "setup_commit_oid": setup_commit_oid,
    }


def approval_path(service: Service) -> Path:
    return service.home / APPROVAL_FILE


def remember_approval(service: Service, project: Any, preparation: dict[str, Any], spec_path: str,
                      *, plan_hash: str | None = None) -> None:
    value = {
        "version": 2 if plan_hash else 1,
        "project_id": project["id"],
        "preparation_id": preparation["preparation_id"],
        "proposal_hash": preparation["proposal_hash"],
        "spec_path": spec_path,
    }
    if plan_hash:
        value["plan_hash"] = plan_hash
    path = approval_path(service)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(canonical(value) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def forget_approval(service: Service) -> None:
    try:
        approval_path(service).unlink()
    except FileNotFoundError:
        pass


def load_approval(service: Service) -> dict[str, Any] | None:
    try:
        value = json.loads(approval_path(service).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    required = {"version", "project_id", "preparation_id", "proposal_hash", "spec_path"}
    if value.get("version") == 1 and set(value) == required:
        return value
    if value.get("version") == 2 and set(value) == required | {"plan_hash"}:
        return value
    return None


def plan_envelope(project: Any, *, spec_path: str, spec_hash: str, starting_oid: str,
                  proposal: dict[str, Any], profile_hashes: dict[str, Any],
                  run_configuration: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "project_id": project["id"],
        "repository_root": project["repository_root"],
        "canonical_branch": project["canonical_branch"],
        "starting_oid": starting_oid,
        "spec_path": spec_path,
        "specification_hash": spec_hash,
        "proposal": proposal,
        "profile_hashes": profile_hashes,
        "run_configuration": run_configuration,
    }


def envelope_hash(value: dict[str, Any]) -> str:
    return "sha256:" + sha256(canonical(value))


def approval_matches(service: Service, project: Any, preparation: Any) -> bool:
    value = load_approval(service)
    matches = bool(
        value and preparation and preparation["state"] == "AWAITING_APPROVAL"
        and value["project_id"] == project["id"]
        and value["preparation_id"] == preparation["id"]
        and value["proposal_hash"] == preparation["proposal_hash"]
    )
    if not matches or value.get("version") != 2:
        return matches
    if not all(key in preparation for key in ("proposal", "profile_hashes", "run_configuration", "specification_hash", "starting_oid")):
        from .controller.initial_planning import preparation_row
        preparation = preparation_row(service, preparation["id"])
    envelope = plan_envelope(
        project, spec_path=value["spec_path"], spec_hash=preparation["specification_hash"],
        starting_oid=preparation["starting_oid"], proposal=preparation["proposal"],
        profile_hashes=preparation["profile_hashes"], run_configuration=preparation["run_configuration"],
    )
    return value["plan_hash"] == envelope_hash(envelope)


def approved_preparation(service: Service, project: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    from .controller.initial_planning import preparation_row

    value = load_approval(service)
    if not value or value.get("project_id") != project["id"]:
        raise LedgerError("APPROVAL_REQUIRED", "No human-approved preparation is ready. Run 'taskledger prepare --spec <path>' first.")
    preparation = preparation_row(service, value["preparation_id"])
    if not approval_matches(service, project, preparation):
        raise LedgerError("PREPARATION_STALE", "The remembered approval no longer matches an awaiting preparation. Prepare and approve the specification again.")
    return preparation, value


def format_proposal(spec_path: str, proposal: dict[str, Any]) -> str:
    policies = {item["task_ref"]: item for item in proposal["execution_policy"]}
    tasks = {item["ref"]: item for item in proposal["tasks"]}
    waves = sorted({item["wave"] for item in policies.values()})
    lines = [
        "Taskledger preparation complete", "", f"Specification: {spec_path}", "",
        f"Requirements: {len(proposal['requirements'])}", f"Tasks: {len(tasks)}", f"Waves: {len(waves)}",
    ]
    for wave in waves:
        lines.extend(("", f"Wave {wave}"))
        for ref, policy in policies.items():
            if policy["wave"] == wave:
                task = tasks[ref]
                lines.extend((
                    f"  {ref} [{policy['worker_profile']}] {task['objective']}",
                    f"    Scope: {task['implementation_scope']}",
                    f"    Requirements: {', '.join(task['requirement_refs'])}",
                    f"    Depends on: {', '.join(task['dependency_refs']) or 'none'}",
                    f"    Parallel safe: {'yes' if policy['parallel_safe'] else 'no'}",
                    f"    Write surfaces: {', '.join(policy['write_surfaces'])}",
                    f"    Acceptance: {'; '.join(task['acceptance_criteria'])}",
                    f"    Checks: {'; '.join(task['required_checks']) or 'none'}",
                ))
    lines.extend(("", "Requirements"))
    for requirement in proposal["requirements"]:
        lines.extend((
            f"  {requirement['ref']} {requirement['statement']}",
            f"    Details: {requirement['details']}",
            f"    Implementation required: {'yes' if requirement['implementation_required'] else 'no'}",
            "    Sources:",
        ))
        for source in requirement["sources"]:
            excerpt = f" — {source['excerpt']}" if source["excerpt"] is not None else ""
            lines.append(f"      - {source['locator']}{excerpt}")
    if not proposal["requirements"]:
        lines.append("  None")
    for heading, key in (("Assumptions", "assumptions"), ("Ambiguities", "ambiguities")):
        lines.extend(("", heading))
        values = proposal[key]
        lines.extend(f"  - {item}" for item in values)
        if not values:
            lines.append("  None")
    return "\n".join(lines)


def _model_request(data: dict[str, Any]) -> tuple[str, Any, dict[str, Any]]:
    from .controller.initial_planning import parse_preparation_request, validate_proposal

    allowed = {"spec_path", "proposal", "limits", "preflight"}
    extra = set(data) - allowed
    if extra:
        raise LedgerError("UNKNOWN_FIELD", "Model plan request contains unknown fields.", details={"fields": sorted(extra)})
    if "spec_path" not in data or "proposal" not in data:
        raise LedgerError("INVALID_REQUEST", "spec_path and proposal are required.")
    spec_path, config, _ = parse_preparation_request({
        "spec_path": data["spec_path"], "live": True,
        **({"limits": data["limits"]} if "limits" in data else {}),
        **({"preflight": data["preflight"]} if "preflight" in data else {}),
    })
    normalized = validate_proposal(json.loads(canonical(data["proposal"])))
    if normalized is None:
        raise LedgerError("INVALID_REQUEST", "The proposed Taskledger plan is invalid.")
    return spec_path, config, normalized


def _spec_bytes(project: Any, spec_path: str) -> tuple[str, bytes]:
    root = Path(project["repository_root"]).resolve()
    try:
        path = (root / spec_path).resolve(strict=True)
    except OSError:
        raise LedgerError("SPECIFICATION_NOT_FOUND", "The specification must be a present file inside the repository.")
    if not path.is_relative_to(root) or not path.is_file():
        raise LedgerError("SPECIFICATION_NOT_FOUND", "The specification must be a present file inside the repository.")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LedgerError("SPECIFICATION_NOT_FOUND", "The specification cannot be read.", details={"reason": exc.__class__.__name__}) from exc
    if len(raw) > MAX_SPEC:
        raise LedgerError("INVALID_REQUEST", "Specification exceeds the 50 MiB limit.")
    return path.relative_to(root).as_posix(), raw


def _resolved_identities(project: Any, runtime_factory=None) -> tuple[dict[str, Any], str]:
    from .controller.app_server import AppServerRuntime
    from .controller.model import SessionRole

    runtime = (runtime_factory or AppServerRuntime)(repository_root=project["repository_root"], worker_tools={})
    identities = {
        "task_creator": runtime.session_identity(role=SessionRole.TASK_CREATOR, profile="taskledger_task_creator", subject_id="preflight", cwd=project["repository_root"], writable=False).as_dict(),
        "routine": runtime.session_identity(role=SessionRole.WORKER, profile="routine", subject_id="preflight", cwd=project["repository_root"], writable=True).as_dict(),
        "complex": runtime.session_identity(role=SessionRole.WORKER, profile="complex", subject_id="preflight", cwd=project["repository_root"], writable=True).as_dict(),
        "reviewer": runtime.session_identity(role=SessionRole.REVIEWER, profile="taskledger_reviewer", subject_id="preflight", cwd=project["repository_root"], writable=False).as_dict(),
    }
    return identities, runtime.protocol_identity


def preview_model_plan(service: Service, project: Any, data: dict[str, Any], *, runtime_factory=None) -> dict[str, Any]:
    spec_path, config, proposal = _model_request(data)
    info = git.inspect(project["repository_root"])
    if not info["has_commits"]:
        raise LedgerError("INITIAL_COMMIT_REQUIRED", "The repository needs an initial commit before plan preview.")
    if info["branch"] != project["canonical_branch"]:
        raise LedgerError("CANONICAL_BRANCH_NOT_CHECKED_OUT", "Plan preview requires the canonical branch.")
    if not git.clean(info["root"]):
        raise LedgerError("CANONICAL_WORKTREE_DIRTY", "Plan preview requires a clean canonical worktree.")
    spec_path, raw = _spec_bytes(project, spec_path)
    diagnostics = service.preparation_diagnostics(project, {
        "profiles": ["routine", "complex"], "local_inputs": config.local_inputs,
        "services": config.services, "host_agent_availability": {"routine": True, "complex": True},
    })
    if not diagnostics["ready_for_local_preparation"]:
        raise LedgerError("INVALID_REQUEST", "Plan preview preflight failed.", details={"preflight": diagnostics})
    identities, protocol_identity = _resolved_identities(project, runtime_factory)
    envelope = plan_envelope(
        project, spec_path=spec_path, spec_hash=sha256(raw), starting_oid=info["head_oid"],
        proposal=proposal, profile_hashes=identities, run_configuration=config.as_dict(),
    )
    status = "NEEDS_REVISION" if proposal["ambiguities"] else "PREVIEW"
    return {
        "status": status, "plan_hash": envelope_hash(envelope), "proposal_hash": "sha256:" + sha256(canonical(proposal)),
        "spec_path": spec_path,
        "specification_hash": sha256(raw), "starting_oid": info["head_oid"], "canonical_branch": info["branch"],
        "normalized_proposal": proposal, "rendered_plan": format_proposal(spec_path, proposal),
        "run_configuration": config.as_dict(), "profiles": identities, "codex_protocol_identity": protocol_identity,
    }


def _activate_exact_specification(service: Service, project: Any, principal: Any, spec_path: str, expected_hash: str):
    existing = service.con.execute(
        "SELECT id,active_revision_id FROM specifications WHERE project_id=? AND relative_path=? AND lifecycle='ACTIVE'",
        (project["id"], spec_path),
    ).fetchone()
    if existing is None:
        created = service.register_spec(project, principal, {"relative_path": spec_path})
        spec_id = created["specification_id"]
        new_spec = True
    else:
        spec_id = existing["id"]
        new_spec = False
        service.check_specs(project, principal, preflight=True)
    pending = service.con.execute(
        "SELECT id FROM specification_reviews WHERE project_id=? AND specification_id=? AND state='PENDING'",
        (project["id"], spec_id),
    ).fetchone()
    if pending:
        requirements = [] if new_spec else [row[0] for row in service.con.execute(
            "SELECT id FROM requirements WHERE project_id=? AND lifecycle='ACTIVE' ORDER BY id", (project["id"],)
        )]
        tasks = [] if new_spec else [row[0] for row in service.con.execute(
            "SELECT id FROM tasks WHERE project_id=? AND state<>'CANCELLED' ORDER BY id", (project["id"],)
        )]
        service.review_spec(project, principal, {
            "review_id": pending["id"], "resolution": "APPROVE_REVISION",
            "affected_requirement_ids": requirements, "affected_task_ids": tasks,
            "no_existing_items_affected": not requirements and not tasks,
            "summary": "The exact specification revision was approved with the invoking-model plan.",
        })
    from .application.mechanics import active_specification_revision
    revision = active_specification_revision(service, project, specification_id=spec_id)
    if revision["content_hash"] != expected_hash:
        raise LedgerError("PREPARATION_STALE", "Specification changed before the approved plan was committed.")
    return revision


def commit_model_plan(service: Service, project: Any, principal: Any, data: dict[str, Any], *, runtime_factory=None) -> dict[str, Any]:
    allowed = {"spec_path", "proposal", "limits", "preflight", "approve_plan_hash"}
    extra = set(data) - allowed
    if extra:
        raise LedgerError("UNKNOWN_FIELD", "Model plan commit contains unknown fields.", details={"fields": sorted(extra)})
    approved = data.get("approve_plan_hash")
    if not isinstance(approved, str) or not approved:
        raise LedgerError("APPROVAL_REQUIRED", "approve_plan_hash is required.")
    preview = preview_model_plan(service, project, {key: value for key, value in data.items() if key != "approve_plan_hash"}, runtime_factory=runtime_factory)
    if preview["status"] != "PREVIEW":
        raise LedgerError("INVALID_REQUEST", "A plan with unresolved ambiguities cannot be committed.")
    if approved != preview["plan_hash"]:
        raise LedgerError("PREPARATION_STALE", "Approved plan hash does not match the current exact plan.")
    if service.con.execute("SELECT 1 FROM controller_runs WHERE project_id=? AND state='RUNNING'", (project["id"],)).fetchone():
        raise LedgerError("INVALID_REQUEST", "A controller run is already active.")
    if service.con.execute("SELECT 1 FROM assignments WHERE project_id=? AND state IN ('PREPARING','ACTIVE')", (project["id"],)).fetchone():
        raise LedgerError("INVALID_REQUEST", "Model plan commit requires no active assignments.")
    preparation_id, stamp = new_id(), now()
    proposal = preview["normalized_proposal"]
    config_json = canonical(preview["run_configuration"])
    with transaction(service.con):
        revision = _activate_exact_specification(service, project, principal, preview["spec_path"], preview["specification_hash"])
        service.con.execute(
            "UPDATE project_preparations SET state='SUPERSEDED',updated_at=? WHERE project_id=? AND state IN ('PLANNING','AWAITING_APPROVAL')",
            (stamp, project["id"]),
        )
        service.con.execute(
            "INSERT INTO project_preparations(id,project_id,planning_run_id,task_creator_turn_id,starting_oid,canonical_branch,specification_id,specification_hash,profile_hashes_json,run_configuration_json,run_configuration_hash,proposal_json,proposal_hash,state,failure_reason,execution_run_id,created_at,updated_at,approved_at,origin) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (preparation_id, project["id"], None, None, preview["starting_oid"], preview["canonical_branch"],
             revision["specification_id"], preview["specification_hash"], canonical(preview["profiles"]), config_json,
             sha256(config_json), canonical(proposal), preview["proposal_hash"], "AWAITING_APPROVAL", None, None,
             stamp, stamp, None, "INVOKING_MODEL"),
        )
        service.audit(project["id"], principal["id"], "MODEL_PLAN_COMMITTED", "PREPARATION", preparation_id,
                      {"plan_hash": preview["plan_hash"], "proposal_hash": preview["proposal_hash"]})
    result = {"preparation_id": preparation_id, "proposal_hash": preview["proposal_hash"]}
    remember_approval(service, project, result, preview["spec_path"], plan_hash=preview["plan_hash"])
    return {"status": "READY", "plan_hash": preview["plan_hash"], **result,
            "next_commands": ["taskledger start", "taskledger ui"]}
