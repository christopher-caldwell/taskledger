from __future__ import annotations

import asyncio
import threading
from contextlib import suppress

from taskledger.controller.journal import Journal
from taskledger.controller.model import RuntimeTurnHandle
from taskledger.controller.project import ProjectController, ProjectControllerResult


class LifecycleOwner:
    """Reusable controller-task ownership and safe interruption reconciliation."""
    def __init__(self, service, project, principal, run_id, config, runtime):
        self.service, self.project, self.principal = service, project, principal
        self.run_id, self.config, self.runtime = run_id, config, runtime
        self.journal = Journal(service.con, service.home)
        self._stop = threading.Event(); self._reason = "USER_INTERRUPTED"; self._loop = None

    def request_stop(self, reason="USER_INTERRUPTED"):
        self._reason = reason; self._stop.set()
        if self._loop: self._loop.call_soon_threadsafe(lambda: None)

    async def run(self):
        self._loop = asyncio.get_running_loop()
        controller = asyncio.create_task(ProjectController(service=self.service, project=self.project,
            orchestrator=self.principal, run_id=self.run_id, runtime=self.runtime, journal=self.journal,
            config=self.config, stop_requested=self._stop.is_set).run())
        async def wait_for_stop():
            while not self._stop.is_set(): await asyncio.sleep(.05)
        stopper = asyncio.create_task(wait_for_stop())
        try:
            done, _ = await asyncio.wait({controller, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if controller in done:
                stopper.cancel(); return await controller
            controller.cancel(); await asyncio.gather(controller, return_exceptions=True)
            unresolved = []
            for turn in self.journal.open_turns(project_id=self.project["id"]):
                result = None
                if turn.external_turn_id:
                    handle = RuntimeTurnHandle(turn.thread_id, turn.external_turn_id)
                    with suppress(Exception): await asyncio.wait_for(self.runtime.interrupt_turn(handle), timeout=5)
                    try:
                        inspection = None
                        for _ in range(10):
                            inspection = await asyncio.wait_for(self.runtime.inspect_turn(handle), timeout=1); result = inspection.result
                            if inspection.state in {"FAILED", "COMPLETED"}: break
                            await asyncio.sleep(.1)
                        if inspection and inspection.state == "FAILED":
                            self.journal.fail_turn(turn.id, inspection.error or "INTERRUPTED", uncertain=False, result=result); self.journal.consume_turn(turn.id); continue
                        if inspection and inspection.state == "COMPLETED" and result is not None:
                            self.journal.complete_turn(turn.id, result); self.journal.consume_turn(turn.id); continue
                    except Exception: pass
                self.journal.fail_turn(turn.id, "interrupted before a terminal provider result was proven", uncertain=True, result=result); unresolved.append(turn.id)
            if (self.journal.run(self.run_id) or {}).get("state") == "RUNNING":
                detail = "unresolved interrupted turns: " + ", ".join(unresolved) if unresolved else "foreground execution interrupted safely"
                self.journal.finish_run(self.run_id, "PAUSED", reason=self._reason, detail=detail)
            return ProjectControllerResult("PAUSED", self.run_id, self._reason, "foreground execution interrupted", usage=self.journal.usage_report(run_id=self.run_id))
        except Exception as exc:
            if (self.journal.run(self.run_id) or {}).get("state") == "RUNNING": self.journal.finish_run(self.run_id, "FAILED", reason="INVALID_STATE", detail=str(exc))
            raise
        finally: await self.runtime.close()
