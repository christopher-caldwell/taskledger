from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from . import git
from .core import LedgerError, MAX_JSON, canonical, require_object, taskledger_home, text
from .db import connect
from .service import Service


def emit(value: dict[str, Any]) -> None:
    sys.stdout.write(canonical(value) + "\n")


def load_input(name: str | None) -> dict[str, Any]:
    if not name: return {}
    try:
        raw = sys.stdin.buffer.read(MAX_JSON + 1) if name == "-" else Path(name).read_bytes()
    except OSError:
        raise LedgerError("INVALID_REQUEST", "JSON input file cannot be read.")
    if len(raw) > MAX_JSON: raise LedgerError("INVALID_REQUEST", "JSON input exceeds the 10 MiB limit.")
    try: value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError): raise LedgerError("INVALID_JSON", "Input is not valid JSON.")
    if not isinstance(value, dict): raise LedgerError("INVALID_REQUEST", "Input must be a JSON object.")
    return value


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(add_help=False, exit_on_error=False)
    p.add_argument("resource", nargs="?");p.add_argument("action", nargs="?")
    p.add_argument("--input");p.add_argument("--token");p.add_argument("--project");p.add_argument("--repo");p.add_argument("--confirm-branch");p.add_argument("--verbose",action="store_true");p.add_argument("--version",action="store_true")
    return p


def list_tasks(service, project, data):
    require_object(data,{"filter"})
    filt=data.get("filter","all")
    rows=[]
    for task in service.con.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY created_at,id",(project["id"],)):
        eligible=service.task_eligible(project,task)[0]
        include=filt=="all" or (filt=="eligible" and eligible) or (filt=="active" and task["state"]=="ASSIGNED") or (filt=="submitted" and task["state"]=="SUBMITTED") or (filt=="accepted" and task["state"]=="ACCEPTED") or (filt=="completed" and task["state"]=="COMPLETED") or (filt=="cancelled" and task["state"]=="CANCELLED") or (filt=="blocked" and bool(service.blocking_reasons(project["id"],"assign",task_id=task["id"])))
        if include: rows.append({"id":task["id"],"revision":task["current_revision"],"state":task["state"],"eligible":eligible})
    return {"tasks":rows}


def list_requirements(service,project,data):
    require_object(data,{"filter"});filt=data.get("filter","all")
    rows=service.requirement_rows(project)
    status={"complete":"COMPLETE","remaining":"REMAINING","blocked":"BLOCKED"}.get(filt)
    if filt not in {"all","complete","remaining","blocked","ready"}:raise LedgerError("INVALID_REQUEST","Unsupported requirement filter.")
    return {"requirements":[x for x in rows if filt=="all" or (filt=="ready" and x["ready_for_verification"]) or x["status"]==status]}


def followup_review(service,project,principal,data):
    require_object(data,{"follow_up_id","state"},{"follow_up_id","state"})
    if data["state"] not in {"ACKNOWLEDGED","DISMISSED"}:raise LedgerError("INVALID_REQUEST","Follow-up state must be ACKNOWLEDGED or DISMISSED.")
    proposal=service.con.execute("SELECT f.* FROM follow_up_proposals f JOIN assignments a ON a.id=f.assignment_id WHERE f.id=? AND a.project_id=?",(data["follow_up_id"],project["id"])).fetchone()
    if not proposal:raise LedgerError("INVALID_REQUEST","Follow-up proposal was not found.")
    from .db import transaction
    from .core import now
    with transaction(service.con):service.con.execute("UPDATE follow_up_proposals SET state=?,reviewed_at=?,reviewed_by_principal_id=? WHERE id=?",(data["state"],now(),principal["id"],proposal["id"]));service.audit(project["id"],principal["id"],"FOLLOW_UP_REVIEWED","FOLLOW_UP",proposal["id"])
    return {"follow_up_id":proposal["id"],"state":data["state"]}


def home_for_repository(info, *, create: bool) -> Path:
    if os.environ.get("TASKLEDGER_HOME"):
        return taskledger_home(create=create)
    common = Path(str(info["common"]))
    repository_root = common.parent if common.name == ".git" else Path(str(info["root"]))
    return taskledger_home(repository_root, create=create)


def init_project(args, data):
    if data: raise LedgerError("UNKNOWN_FIELD","project init only accepts --repo and --confirm-branch flags.")
    if not args.repo:raise LedgerError("INVALID_REQUEST","project init requires --repo.")
    info=git.inspect(args.repo);home=home_for_repository(info,create=False);database=home/"taskledger.sqlite3"
    local_default=not os.environ.get("TASKLEDGER_HOME")
    ignored=not local_default or git.ignored(info["root"],".taskledger/")
    if not args.confirm_branch and not database.is_file():
        return {"repository_root":info["root"],"detected_branch":info["branch"],"has_commits":info["has_commits"],"confirmation_required":True,"ledger_directory":str(home),"ledger_directory_ignored":ignored}
    if args.confirm_branch and not database.is_file() and args.confirm_branch != info["branch"]:
        raise LedgerError("BRANCH_CONFIRMATION_MISMATCH","Confirmation must exactly match the detected branch.",details={"detected_branch":info["branch"]})
    if args.confirm_branch and not database.is_file() and not ignored:
        raise LedgerError(
            "LEDGER_DIRECTORY_NOT_IGNORED",
            "Add .taskledger/ to this repository's ignore rules before initializing Taskledger.",
            details={"ledger_directory":str(home)},
        )
    home=home_for_repository(info,create=bool(args.confirm_branch));service=Service(connect(home),home)
    result=service.init(args.repo,args.confirm_branch)
    result.update({"ledger_directory":str(home),"ledger_directory_ignored":ignored})
    return result


def service_for_command(args) -> Service:
    if os.environ.get("TASKLEDGER_HOME"):
        home=taskledger_home(create=False)
    else:
        try:info=git.inspect(os.getcwd())
        except LedgerError:
            raise LedgerError("PROJECT_REQUIRED","Run Taskledger from the managed repository or set TASKLEDGER_HOME to its configured current store.")
        home=home_for_repository(info,create=False)
    if not (home/"taskledger.sqlite3").is_file():
        raise LedgerError("PROJECT_REQUIRED","Taskledger is not initialized for this repository.",details={"ledger_directory":str(home)})
    return Service(connect(home),home)


def project_controller_config(limits):
    from .controller.project import ProjectControllerConfig
    from .controller.supervisor import SupervisorConfig
    require_object(limits,{"max_workers","max_reviewers","max_total_worker_turns","max_total_reviewer_turns","reviewer_token_reserve","max_final_reviewer_turns","max_task_creator_turns","max_total_task_creator_turns","max_inflight_targets","task_creator_profile","supervisor"})
    supervisor=limits.get("supervisor",{});require_object(supervisor,{"max_worker_turns","max_consecutive_stalled_turns","max_consecutive_runtime_failures","max_reviewer_turns_per_submission","max_total_tokens","max_elapsed_seconds","turn_timeout_seconds","reviewer_profile"})
    try:
        config=ProjectControllerConfig(
            max_workers=limits.get("max_workers",2),max_reviewers=limits.get("max_reviewers",1),
            max_total_worker_turns=limits.get("max_total_worker_turns",100),max_total_reviewer_turns=limits.get("max_total_reviewer_turns",50),
            reviewer_token_reserve=limits.get("reviewer_token_reserve",0),
            max_final_reviewer_turns=limits.get("max_final_reviewer_turns",2),max_task_creator_turns=limits.get("max_task_creator_turns",2),
            max_total_task_creator_turns=limits.get("max_total_task_creator_turns",6),max_inflight_targets=limits.get("max_inflight_targets"),
            task_creator_profile=limits.get("task_creator_profile","taskledger_task_creator"),supervisor=SupervisorConfig(**supervisor),
        );config.validate();return config
    except (TypeError,ValueError) as exc:raise LedgerError("INVALID_REQUEST",str(exc))


def run_project_controller(service,project,principal,run_id,config,runtime):
    from .controller.journal import Journal
    from .controller.project import ProjectController
    journal=Journal(service.con,service.home)
    async def execute():
        try:return await ProjectController(service=service,project=project,orchestrator=principal,run_id=run_id,runtime=runtime,journal=journal,config=config).run()
        except Exception as exc:
            if (journal.run(run_id) or {}).get("state")=="RUNNING":journal.finish_run(run_id,"FAILED",reason="INVALID_STATE",detail=str(exc))
            raise
        finally:await runtime.close()
    return asyncio.run(execute())


def dispatch(args, data):
    command=f"{args.resource}.{args.action}"
    if command=="project.init":return init_project(args,data)
    if not args.resource or not args.action:raise LedgerError("INVALID_REQUEST","Command requires a resource and action.")
    service=service_for_command(args)
    worker=command.startswith("worker.")
    if worker:
        principal=service.authenticate(None,args.token,"WORKER");project,assignment=service.worker_assignment(principal)
        if command=="worker.context":return service.worker_context(principal,data)
        if command=="worker.check":return service.worker_check(principal,data)
        if command=="worker.checkpoint":return service.worker_checkpoint(principal,data)
        if command=="worker.artifact-register":return service.register_artifact(project,principal,data,worker=True)
        if command=="worker.question":return service.worker_question(principal,data)
        if command=="worker.blocker":return service.blocker_create(project,principal,data,worker=True)
        if command=="worker.follow-up":return service.worker_followup(principal,data)
        if command=="worker.submit":return service.worker_submit(principal,data)
        raise LedgerError("INVALID_REQUEST","Unknown worker command.")
    project,principal=service.auth_orchestrator(args.project,args.token)
    if command not in {"project.show","project.recover","controller.show","controller.report"}:service.preflight(project)
    if command=="project.show":
        service.preflight(project);valid,fingerprint,diagnostics=service.plan_current(project["id"])
        return {"project_id":project["id"],"repository_root":project["repository_root"],"canonical_branch":project["canonical_branch"],"effective_phase":service.phase(project),"plan":{"valid":valid,"fingerprint":fingerprint,"diagnostics":diagnostics},"progress":service.progress(project),"pending_reviews":service.con.execute("SELECT COUNT(*) FROM specification_reviews WHERE project_id=? AND state='PENDING'",(project["id"],)).fetchone()[0]}
    if command=="project.recover":require_object(data,{});return service.recover(project)
    if command=="project.resume":return service.resume(project,data)
    if command=="project.wait":return service.wait_for_events(project,data)
    if command=="project.preflight":return service.preparation_diagnostics(project,data)
    if command=="project.set-canonical-branch":return service.set_branch(project,principal,data)
    if command=="project.complete":require_object(data,{});return service.complete_project(project,principal)
    if command=="project.cleanup":require_object(data,{});return service.cleanup_worktrees(project,principal)
    if command=="controller.run-project":
        require_object(data,{"execution_policy","live","limits"},{"execution_policy","live"})
        if data["live"] is not True:raise LedgerError("INVALID_REQUEST","Live controller execution requires live=true explicit opt in.")
        from .controller.app_server import AppServerRuntime
        from .controller.journal import Journal
        from .controller.model import SessionRole
        from .controller.project import validate_execution_policy
        try:targets=validate_execution_policy(service,project,data["execution_policy"])
        except ValueError as exc:raise LedgerError("INVALID_REQUEST",str(exc))
        config=project_controller_config(data.get("limits",{}))
        runtime=AppServerRuntime(repository_root=project["repository_root"],worker_tools={})
        # Resolve every semantic identity before the durable run becomes RUNNING.
        for profile in sorted({target["worker_profile"] for target in targets}):
            runtime.session_identity(role=SessionRole.WORKER,profile=profile,subject_id="preflight",cwd=project["repository_root"],writable=True)
        runtime.session_identity(role=SessionRole.REVIEWER,profile=config.supervisor.reviewer_profile,subject_id="preflight",cwd=project["repository_root"],writable=False)
        runtime.session_identity(role=SessionRole.REQUIREMENT_REVIEWER,profile=config.supervisor.reviewer_profile,subject_id="preflight",cwd=project["repository_root"],writable=False)
        runtime.session_identity(role=SessionRole.TASK_CREATOR,profile=config.task_creator_profile,subject_id="preflight",cwd=project["repository_root"],writable=False)
        journal=Journal(service.con,service.home)
        valid,plan_fingerprint,_=service.plan_current(project["id"])
        if not valid or not plan_fingerprint:raise LedgerError("PLAN_INVALID","Approved Taskledger plan is not current.")
        manifest={
            "canonical_starting_oid":git.oid(project["repository_root"],f"refs/heads/{project['canonical_branch']}"),
            "starting_plan_fingerprint":plan_fingerprint,
            "execution_policy_hash":hashlib.sha256(canonical(targets).encode()).hexdigest(),
            "controller_configuration_hash":hashlib.sha256(canonical(config.as_dict()).encode()).hexdigest(),
            "taskledger_schema_version":service.con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
            "codex_protocol_identity":runtime.protocol_identity,
            "scope":"POST_APPROVAL_EXECUTION",
        }
        try:run_id=journal.create_project_run(project["id"],config={"limits":config.as_dict(),"manifest":manifest},targets=targets)
        except RuntimeError as exc:raise LedgerError("INVALID_REQUEST",str(exc))
        result=run_project_controller(service,project,principal,run_id,config,runtime)
        return asdict(result)
    if command=="controller.run-assignment":
        require_object(data,{"assignment_id","live","limits"},{"assignment_id","live"})
        if data["live"] is not True:raise LedgerError("INVALID_REQUEST","Live controller execution requires live=true explicit opt in.")
        limits=data.get("limits",{});require_object(limits,{"max_worker_turns","max_consecutive_stalled_turns","max_consecutive_runtime_failures","max_reviewer_turns_per_submission","max_total_tokens","max_elapsed_seconds","turn_timeout_seconds","reviewer_profile"})
        from .controller.app_server import AppServerRuntime
        from .controller.journal import Journal
        from .controller.supervisor import Supervisor,SupervisorConfig
        from .controller.taskledger_adapter import TaskledgerLedgerAdapter
        try:config=SupervisorConfig(**limits);config.validate()
        except (TypeError,ValueError) as exc:raise LedgerError("INVALID_REQUEST",str(exc))
        assignment_id=text(data["assignment_id"],"assignment_id")
        assignment=service.con.execute("SELECT * FROM assignments WHERE id=? AND project_id=?",(assignment_id,project["id"])).fetchone()
        if not assignment:raise LedgerError("ASSIGNMENT_NOT_ACTIVE","Assignment was not found.")
        from .controller.worker_broker import WorkerBroker,broker_socket_path
        token_path=service.home/"projects"/project["id"]/"assignments"/assignment_id/"worker-token"
        try:worker=service.authenticate(project["id"],token_path.read_text().strip(),"WORKER")
        except OSError:raise LedgerError("ASSIGNMENT_NOT_ACTIVE","Active assignment worker credential is unavailable.")
        socket_path=broker_socket_path(service.home,assignment_id);broker=WorkerBroker(service,worker,socket_path)
        runtime=AppServerRuntime(repository_root=project["repository_root"],worker_tools={assignment_id:broker.dispatch})
        runtime.session_identity(role=SessionRole.WORKER,profile=assignment["worker_profile"],subject_id=assignment_id,cwd=assignment["worktree_path"],writable=True)
        journal=Journal(service.con,service.home)
        valid,plan_fingerprint,_=service.plan_current(project["id"])
        manifest={"canonical_starting_oid":git.oid(project["repository_root"]),"starting_plan_fingerprint":plan_fingerprint if valid else None,
                  "execution_policy_hash":None,"controller_configuration_hash":hashlib.sha256(canonical(asdict(config)).encode()).hexdigest(),
                  "taskledger_schema_version":service.con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                  "codex_protocol_identity":runtime.protocol_identity,"scope":"POST_APPROVAL_EXECUTION"}
        try:run_id=journal.create_run(project["id"],mode="ASSIGNMENT",config={"assignment_id":assignment_id,"limits":asdict(config),"manifest":manifest})
        except RuntimeError as exc:raise LedgerError("INVALID_REQUEST",str(exc))
        async def execute():
            try:
                adapter=TaskledgerLedgerAdapter(service,project,principal,worker_tool_enabled=True)
                return await Supervisor(project_id=project["id"],run_id=run_id,ledger=adapter,runtime=runtime,journal=journal,config=config).run_assignment(assignment_id)
            except Exception as exc:
                journal.finish_run(run_id,"FAILED",reason="INVALID_STATE",detail=str(exc));raise
            finally:
                await runtime.close()
                await broker.close()
        result=asyncio.run(execute());return {"run_id":run_id,**asdict(result)}
    if command=="controller.resume":
        require_object(data,{"run_id","live"},{"run_id","live"})
        if data["live"] is not True:raise LedgerError("INVALID_REQUEST","Live controller execution requires live=true explicit opt in.")
        from .controller.app_server import AppServerRuntime
        from .controller.journal import Journal
        from .controller.model import SessionRole
        from .controller.supervisor import Supervisor,SupervisorConfig
        from .controller.taskledger_adapter import TaskledgerLedgerAdapter
        journal=Journal(service.con,service.home);requested_id=text(data["run_id"],"run_id");existing=journal.run(requested_id)
        if not existing or existing["project_id"]!=project["id"]:raise LedgerError("INVALID_REQUEST","Controller run does not belong to this project.")
        if existing["mode"]=="PROJECT":
            config=project_controller_config(existing["config"]["limits"])
            runtime=AppServerRuntime(repository_root=project["repository_root"],worker_tools={})
            # Validate configured profiles before changing durable run state.
            for target in journal.targets(requested_id):
                if target["state"] not in {"INTEGRATED","CANCELLED"}:
                    runtime.session_identity(role=SessionRole.WORKER,profile=target["worker_profile"],subject_id="preflight",cwd=project["repository_root"],writable=True)
            runtime.session_identity(role=SessionRole.REVIEWER,profile=config.supervisor.reviewer_profile,subject_id="preflight",cwd=project["repository_root"],writable=False)
            runtime.session_identity(role=SessionRole.TASK_CREATOR,profile=config.task_creator_profile,subject_id="preflight",cwd=project["repository_root"],writable=False)
            try:run=journal.resume_run(requested_id)
            except RuntimeError as exc:raise LedgerError("INVALID_REQUEST",str(exc))
            return asdict(run_project_controller(service,project,principal,run["id"],config,runtime))
        if existing["mode"]!="ASSIGNMENT":raise LedgerError("INVALID_REQUEST","Unsupported controller run mode.")
        assignment_id=existing["config"]["assignment_id"];config=SupervisorConfig(**existing["config"]["limits"])
        assignment=service.con.execute("SELECT * FROM assignments WHERE id=? AND project_id=?",(assignment_id,project["id"])).fetchone()
        if not assignment:raise LedgerError("ASSIGNMENT_NOT_ACTIVE","Assignment was not found.")
        from .controller.worker_broker import WorkerBroker,broker_socket_path
        token_path=service.home/"projects"/project["id"]/"assignments"/assignment_id/"worker-token"
        try:worker=service.authenticate(project["id"],token_path.read_text().strip(),"WORKER")
        except OSError:raise LedgerError("ASSIGNMENT_NOT_ACTIVE","Active assignment worker credential is unavailable.")
        broker=WorkerBroker(service,worker,broker_socket_path(service.home,assignment_id))
        runtime=AppServerRuntime(repository_root=project["repository_root"],worker_tools={assignment_id:broker.dispatch})
        runtime.session_identity(role=SessionRole.WORKER,profile=assignment["worker_profile"],subject_id=assignment_id,cwd=assignment["worktree_path"],writable=True)
        try:run=journal.resume_run(requested_id)
        except RuntimeError as exc:raise LedgerError("INVALID_REQUEST",str(exc))
        async def resume_execution():
            try:
                adapter=TaskledgerLedgerAdapter(service,project,principal,worker_tool_enabled=True)
                return await Supervisor(project_id=project["id"],run_id=run["id"],ledger=adapter,runtime=runtime,journal=journal,config=config).run_assignment(assignment_id)
            except Exception as exc:
                journal.finish_run(run["id"],"FAILED",reason="INVALID_STATE",detail=str(exc));raise
            finally:
                await runtime.close()
                await broker.close()
        result=asyncio.run(resume_execution());return {"run_id":run["id"],**asdict(result)}
    if command=="controller.show":
        require_object(data,{"run_id"},{"run_id"})
        from .controller.journal import Journal
        journal=Journal(service.con,service.home);run=journal.run(text(data["run_id"],"run_id"))
        if not run or run["project_id"]!=project["id"]:raise LedgerError("INVALID_REQUEST","Controller run was not found.")
        sessions=[]
        for row in service.con.execute("SELECT id,role,profile,subject_id,external_thread_id,runtime_identity_json,state,created_at,closed_at FROM controller_sessions WHERE run_id=? ORDER BY created_at,id",(run["id"],)):
            item=dict(row);identity=json.loads(item.pop("runtime_identity_json"));item["runtime_identity"]=identity
            item["profile_resolution"]={"role":item["role"],"profile":identity.get("profile_name",item["profile"]),"native_role_name":identity.get("profile_role_name"),"source":identity.get("profile_source_kind"),"source_file":identity.get("profile_source_file"),"model":identity.get("model"),"effort":identity.get("effort"),"config_hash":identity.get("agent_config_hash"),"profile_hash":identity.get("profile_hash"),"protocol_identity":identity.get("protocol_identity"),"sandbox":identity.get("sandbox")}
            sessions.append(item)
        return {"run":run,"targets":journal.targets(run["id"]) if run["mode"]=="PROJECT" else [],"sessions":sessions,"usage":journal.usage_report(run_id=run["id"])}
    if command=="controller.report":
        require_object(data,{"run_id","include_provider_detail"},{"run_id"})
        from .controller.reporting import controller_report
        run_id=text(data["run_id"],"run_id")
        run=service.con.execute("SELECT project_id FROM controller_runs WHERE id=?",(run_id,)).fetchone()
        if not run or run["project_id"]!=project["id"]:raise LedgerError("INVALID_REQUEST","Controller run was not found.")
        include=data.get("include_provider_detail",True)
        if not isinstance(include,bool):raise LedgerError("INVALID_REQUEST","include_provider_detail must be boolean.")
        async def build_report():
            if not include:return await controller_report(service.con,run_id,include_provider_detail=False)
            from .controller.app_server import AppServerRuntime
            runtime=None
            try:
                runtime=AppServerRuntime(repository_root=project["repository_root"])
                return await controller_report(service.con,run_id,include_provider_detail=True,provider_loader=runtime.provider_history)
            except Exception:
                # Provider diagnostics are optional enrichment. Construction,
                # version probing, history reads, and cleanup must never make
                # the durable core report unavailable.
                return await controller_report(service.con,run_id,include_provider_detail=True)
            finally:
                if runtime is not None:
                    try:await runtime.close()
                    except Exception:pass
        return asyncio.run(build_report())
    if command=="controller.extend-budget":
        require_object(data,{"run_id","kind","amount","reason"},{"run_id","kind","amount","reason"})
        from .controller.journal import Journal
        journal=Journal(service.con,service.home);run=journal.run(text(data["run_id"],"run_id"))
        if not run or run["project_id"]!=project["id"]:raise LedgerError("INVALID_REQUEST","Controller run was not found.")
        try:grant_id=journal.grant_budget(run_id=run["id"],kind=text(data["kind"],"kind"),amount=data["amount"],reason=text(data["reason"],"reason"),principal_id=principal["id"])
        except ValueError as exc:raise LedgerError("INVALID_REQUEST",str(exc))
        return {"run_id":run["id"],"grant_id":grant_id,"kind":data["kind"],"amount":data["amount"]}
    if command=="spec.register":return service.register_spec(project,principal,data)
    if command=="spec.check":require_object(data,{});return service.check_specs(project,principal)
    if command=="spec.review":return service.review_spec(project,principal,data)
    if command=="requirement.create":return service.create_requirement(project,principal,data)
    if command=="requirement.update":return service.update_requirement(project,principal,data)
    if command=="requirement.retire":return service.update_requirement(project,principal,data,retire=True)
    if command=="requirement.supersede":return service.supersede_requirement(project,principal,data)
    if command=="requirement.list":return list_requirements(service,project,data)
    if command=="requirement.verify":return service.requirement_verify(project,principal,data)
    if command=="requirement.invalidate":return service.invalidate_requirement(project,principal,data)
    if command=="task.create":return service.create_task(project,principal,data)
    if command=="task.update":return service.update_task(project,principal,data)
    if command=="task.cancel":return service.cancel_task(project,principal,data)
    if command=="task.reopen":return service.update_task(project,principal,data,reopen=True)
    if command=="task.list":return list_tasks(service,project,data)
    if command=="task.integrate":return service.integrate_task(project,principal,data)
    if command=="plan.apply":return service.apply_plan(project,principal,data)
    if command=="plan.validate":require_object(data,{});return service.validate_plan(project,principal)
    if command=="assignment.create":return service.assignment_create(project,principal,data)
    if command=="assignment.revoke":return service.assignment_revoke(project,principal,data)
    if command=="assignment.rotate-token":return service.rotate_token(project,principal,data)
    if command=="submission.review-context":require_object(data,{"submission_id"},{"submission_id"});return service.submission_review_context(project,data["submission_id"])
    if command=="submission.check":return service.reviewer_check(project,principal,data)
    if command=="submission.verify":return service.verify_submission(project,principal,data)
    if command=="checkpoint.review-context":require_object(data,{"checkpoint_id"},{"checkpoint_id"});return service.checkpoint_review_context(project,data["checkpoint_id"])
    if command=="checkpoint.verify":return service.verify_checkpoint(project,principal,data)
    if command=="artifact.register":return service.register_artifact(project,principal,data)
    if command=="artifact.list":
        require_object(data,{})
        return {"artifacts":[dict(x) for x in service.con.execute("SELECT id,assignment_id,provenance_kind,original_path,stored_path,sha256,size_bytes,source_revision,created_at FROM evidence_artifacts WHERE project_id=? ORDER BY created_at,id",(project["id"],))]}
    if command=="evidence.export":return service.evidence_export(project,principal,data)
    if command=="blocker.create":return service.blocker_create(project,principal,data)
    if command=="blocker.resolve":return service.resolve_blocker(project,principal,data)
    if command=="blocker.list":
        require_object(data,{"state"});state=data.get("state","open").upper();return {"blockers":[dict(x) for x in service.con.execute("SELECT * FROM blockers WHERE project_id=? AND (?='ALL' OR state=?) ORDER BY id",(project["id"],state,state))]}
    if command=="question.answer":return service.answer_question(project,principal,data)
    if command=="follow-up.list":
        require_object(data,{"state"});state=data.get("state","PROPOSED");return {"follow_up_proposals":[dict(x) for x in service.con.execute("SELECT f.* FROM follow_up_proposals f JOIN assignments a ON a.id=f.assignment_id WHERE a.project_id=? AND (?='ALL' OR f.state=?) ORDER BY f.created_at,f.id",(project["id"],state.upper(),state.upper()))]}
    if command=="follow-up.review":return followup_review(service,project,principal,data)
    if command=="operation.adopt-success":return service.recover_operation(project,principal,data,True)
    if command=="operation.mark-failed":return service.recover_operation(project,principal,data,False)
    raise LedgerError("INVALID_REQUEST","Unknown command.")


def main(argv=None):
    # URL-safe Base64 credentials may legitimately begin with '-'.  Normalize
    # the separated spelling so argparse never mistakes such a token for a flag.
    raw_argv=list(sys.argv[1:] if argv is None else argv)
    normalized=[]; index=0
    while index < len(raw_argv):
        if raw_argv[index] == "--token" and index + 1 < len(raw_argv):
            normalized.append("--token=" + raw_argv[index + 1]); index += 2
        else:
            normalized.append(raw_argv[index]); index += 1
    try:
        args,unknown=parser().parse_known_args(normalized)
        command=f"{args.resource}.{args.action}" if args.resource and args.action else "unknown"
        if unknown:raise LedgerError("INVALID_REQUEST","Unknown command-line flag.",details={"flags":unknown})
        if args.version:
            if args.resource or args.action or any((args.input,args.token,args.project,args.repo,args.confirm_branch)):
                raise LedgerError("INVALID_REQUEST","--version cannot be combined with another command or flag.")
            command="version";result={"version":__version__}
        else:result=dispatch(args,load_input(args.input))
    except argparse.ArgumentError as exc:
        command="unknown"
        emit({"ok":False,"command":command,"error":{"code":"INVALID_REQUEST","message":str(exc),"details":{},"allowed_actions":[]}});return 2
    except LedgerError as exc:
        emit({"ok":False,"command":command,"error":{"code":exc.code,"message":exc.message,"details":exc.details,"allowed_actions":exc.actions}});return exc.exit_code
    except Exception as exc:
        if "args" in locals() and args.verbose:print(f"taskledger internal error: {exc.__class__.__name__}",file=sys.stderr)
        emit({"ok":False,"command":command,"error":{"code":"INTERNAL_ERROR","message":"Taskledger encountered an unexpected internal error.","details":{},"allowed_actions":[]}});return 70
    emit({"ok":True,"command":command,"data":result,"warnings":[]});return 0


if __name__=="__main__":raise SystemExit(main())
