from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static, TabbedContent, TabPane

from .formatting import safe_text


class TaskledgerApp(App):
    CSS = """#summary { height: 3; padding: 0 1 } .pane { padding: 1 } #minimum { display: none; color: yellow }"""
    BINDINGS = [("f1","tab('overview')","Overview"),("f2","tab('tasks')","Tasks"),("f3","tab('agents')","Agents"),("f4","tab('activity')","Activity"),("f5","tab('usage')","Usage"),("f6","tab('interventions')","Interventions"),("ctrl+p","pause","Pause"),("ctrl+q","quit_guarded","Exit")]

    def __init__(self, client): super().__init__(); self.client=client; self.reader=None; self.snapshot=None; self._observer=None
    def compose(self) -> ComposeResult:
        yield Header(); yield Static("",id="minimum"); yield Static("Loading Taskledger…",id="summary")
        with TabbedContent(id="sections"):
            for ident,title in (("overview","Overview"),("tasks","Tasks"),("agents","Agents / turns"),("activity","Activity"),("usage","Usage"),("interventions","Interventions / preparation")):
                with TabPane(title,id=ident): yield Static("Loading…",id=f"{ident}-body",classes="pane")
        yield Footer()

    async def on_mount(self):
        self.reader=self.client.open_feed(); self._observer=asyncio.create_task(self._observe())
    async def _observe(self):
        try:
            while True: self._render(await self.reader.receive())
        except StopAsyncIteration: pass
        except Exception as exc: self.notify(f"Observation unavailable: {safe_text(exc)}",severity="error")
    def _render(self,s):
        if self.snapshot and s.version.engine_epoch==self.snapshot.version.engine_epoch and s.version.revision<=self.snapshot.version.revision:return
        self.snapshot=s; run=s.run.get("state","No run") if s.run else "No run"
        self.query_one("#summary",Static).update(f"{safe_text(s.project.get('repository_root',''))}  •  {safe_text(run)}  •  revision {s.version.revision}")
        self.query_one("#overview-body",Static).update(f"Project: {safe_text(s.project.get('id'))}\nPhase: {safe_text(s.project.get('effective_phase','unknown'))}\nRun: {safe_text(run)}")
        self.query_one("#tasks-body",Static).update("\n".join(f"{safe_text(x.get('state')):12} {safe_text(x.get('objective'))}" for x in s.tasks) or "No tasks")
        self.query_one("#agents-body",Static).update("\n".join(f"{safe_text(x.get('role'))}: {safe_text(x.get('state'))} / {safe_text(x.get('latest_turn_state','idle'))}" for x in s.sessions) or "No sessions")
        self.query_one("#activity-body",Static).update("\n".join(f"{safe_text(x.get('source'))} {safe_text(x.get('event_type'))}" for x in s.activity) or "No activity")
        self.query_one("#usage-body",Static).update(safe_text(dict(s.usage.fields)))
        self.query_one("#interventions-body",Static).update("\n".join(f"{safe_text(x.get('kind'))}: {safe_text(x.get('description'))}" for x in s.interventions) or "No open interventions")
    def action_tab(self,tab): self.query_one("#sections",TabbedContent).active=tab
    async def action_pause(self):
        if self.snapshot and self.snapshot.run: await self.client.request_pause(self.snapshot.run.get("id"))
    async def action_quit_guarded(self):
        if self.snapshot and self.snapshot.run and self.snapshot.run.get("owned_by_host") and self.snapshot.run.get("state")=="RUNNING":
            await self.client.request_pause(self.snapshot.run.get("id"))
        self.exit()
    async def on_unmount(self):
        if self.reader:self.reader.close()
        if self._observer:self._observer.cancel()


async def run_ui(client):
    app=TaskledgerApp(client)
    try: await app.run_async()
    finally: await client.close()
