from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .core import LedgerError, MAX_JSON, canonical, require_object, text
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
    p.add_argument("--input");p.add_argument("--token");p.add_argument("--project");p.add_argument("--repo");p.add_argument("--confirm-branch");p.add_argument("--verbose",action="store_true")
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
    return {"requirements":[x for x in rows if filt=="all" or x["status"]==status]}


def followup_review(service,project,principal,data):
    require_object(data,{"follow_up_id","state"},{"follow_up_id","state"})
    if data["state"] not in {"ACKNOWLEDGED","DISMISSED"}:raise LedgerError("INVALID_REQUEST","Follow-up state must be ACKNOWLEDGED or DISMISSED.")
    proposal=service.con.execute("SELECT f.* FROM follow_up_proposals f JOIN assignments a ON a.id=f.assignment_id WHERE f.id=? AND a.project_id=?",(data["follow_up_id"],project["id"])).fetchone()
    if not proposal:raise LedgerError("INVALID_REQUEST","Follow-up proposal was not found.")
    from .db import transaction
    from .core import now
    with transaction(service.con):service.con.execute("UPDATE follow_up_proposals SET state=?,reviewed_at=?,reviewed_by_principal_id=? WHERE id=?",(data["state"],now(),principal["id"],proposal["id"]));service.audit(project["id"],principal["id"],"FOLLOW_UP_REVIEWED","FOLLOW_UP",proposal["id"])
    return {"follow_up_id":proposal["id"],"state":data["state"]}


def dispatch(args, data):
    service=Service(connect()); command=f"{args.resource}.{args.action}"
    if command=="project.init":
        if data: raise LedgerError("UNKNOWN_FIELD","project init only accepts --repo and --confirm-branch flags.")
        if not args.repo:raise LedgerError("INVALID_REQUEST","project init requires --repo.")
        return service.init(args.repo,args.confirm_branch)
    if not args.resource or not args.action:raise LedgerError("INVALID_REQUEST","Command requires a resource and action.")
    worker=command.startswith("worker.")
    if worker:
        principal=service.authenticate(None,args.token,"WORKER");project,assignment=service.worker_assignment(principal)
        if command=="worker.context":require_object(data,{});return service.worker_context(principal)
        if command=="worker.question":return service.worker_question(principal,data)
        if command=="worker.blocker":return service.blocker_create(project,principal,data,worker=True)
        if command=="worker.follow-up":return service.worker_followup(principal,data)
        if command=="worker.submit":return service.worker_submit(principal,data)
        raise LedgerError("INVALID_REQUEST","Unknown worker command.")
    project,principal=service.auth_orchestrator(args.project,args.token)
    if command not in {"project.show","project.recover"}:service.preflight(project)
    if command=="project.show":
        service.preflight(project);valid,fingerprint,diagnostics=service.plan_current(project["id"])
        return {"project_id":project["id"],"repository_root":project["repository_root"],"canonical_branch":project["canonical_branch"],"effective_phase":service.phase(project),"plan":{"valid":valid,"fingerprint":fingerprint,"diagnostics":diagnostics},"progress":service.progress(project),"pending_reviews":service.con.execute("SELECT COUNT(*) FROM specification_reviews WHERE project_id=? AND state='PENDING'",(project["id"],)).fetchone()[0]}
    if command=="project.recover":require_object(data,{});return service.recover(project)
    if command=="project.set-canonical-branch":return service.set_branch(project,principal,data)
    if command=="project.complete":require_object(data,{});return service.complete_project(project,principal)
    if command=="spec.register":return service.register_spec(project,principal,data)
    if command=="spec.check":require_object(data,{});return service.check_specs(project,principal)
    if command=="spec.review":return service.review_spec(project,principal,data)
    if command=="requirement.create":return service.create_requirement(project,principal,data)
    if command=="requirement.update":return service.update_requirement(project,principal,data)
    if command=="requirement.retire":return service.update_requirement(project,principal,data,retire=True)
    if command=="requirement.list":return list_requirements(service,project,data)
    if command=="requirement.verify":return service.requirement_verify(project,principal,data)
    if command=="requirement.invalidate":return service.invalidate_requirement(project,principal,data)
    if command=="task.create":return service.create_task(project,principal,data)
    if command=="task.update":return service.update_task(project,principal,data)
    if command=="task.cancel":return service.cancel_task(project,principal,data)
    if command=="task.reopen":return service.update_task(project,principal,data,reopen=True)
    if command=="task.list":return list_tasks(service,project,data)
    if command=="task.integrate":return service.integrate_task(project,principal,data)
    if command=="plan.validate":require_object(data,{});return service.validate_plan(project,principal)
    if command=="assignment.create":return service.assignment_create(project,principal,data)
    if command=="assignment.revoke":return service.assignment_revoke(project,principal,data)
    if command=="assignment.rotate-token":return service.rotate_token(project,principal,data)
    if command=="submission.verify":return service.verify_submission(project,principal,data)
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
        result=dispatch(args,load_input(args.input))
    except argparse.ArgumentError as exc:
        command="unknown"
        emit({"ok":False,"command":command,"error":{"code":"INVALID_REQUEST","message":str(exc),"details":{},"allowed_actions":[]}});return 2
    except LedgerError as exc:
        emit({"ok":False,"command":command,"error":{"code":exc.code,"message":exc.message,"details":exc.details,"allowed_actions":exc.actions}});return exc.exit_code
    except Exception as exc:
        if args.verbose:print(f"taskledger internal error: {exc.__class__.__name__}",file=sys.stderr)
        emit({"ok":False,"command":command,"error":{"code":"INTERNAL_ERROR","message":"Internal error.","details":{},"allowed_actions":["project.recover"]}});return 70
    emit({"ok":True,"command":command,"data":result,"warnings":[]});return 0


if __name__=="__main__":raise SystemExit(main())
