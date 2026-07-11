"""Vigilus TUI — a Textual terminal interface with full feature parity.

Usage::

    vigilus chat [--url http://localhost:8000]
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option

from vigilus.tui.client import ApiError, VIgilusClient
from vigilus.tui.config import (
    clear_token,
    get_base_url,
    get_token,
    get_username,
    save_token,
)

# ── Custom message types ───────────────────────────────────────────────────────


@dataclass
class ChatMessage:
    role: str  # 'user' | 'assistant' | 'system' | 'error'
    text: str
    streaming: bool = False


# ── Login screen ──────────────────────────────────────────────────────────────


class LoginScreen(Screen[None]):
    """Username + password login form."""

    BINDINGS = [Binding("ctrl+c", "app.quit", "Quit")]
    DEFAULT_CSS = """
    LoginScreen {
        align: center middle;
    }
    #login-box {
        width: 50;
        height: auto;
        border: solid $primary;
        padding: 1 2;
    }
    #login-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
        color: $primary;
    }
    #login-url {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }
    #login-error {
        color: $error;
        margin-top: 1;
        text-align: center;
        display: none;
    }
    #login-error.visible {
        display: block;
    }
    .login-label {
        margin-top: 1;
    }
    #login-btn {
        margin-top: 1;
        width: 100%;
    }
    """

    class LoggedIn(Message):
        def __init__(self, client: VIgilusClient, username: str) -> None:
            super().__init__()
            self.client = client
            self.username = username

    def __init__(self, base_url: str, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self.base_url = base_url

    def compose(self) -> ComposeResult:
        with Container(id="login-box"):
            yield Label("◈ Vigilus", id="login-title")
            yield Label(self.base_url, id="login-url")
            yield Label("Username", classes="login-label")
            yield Input(placeholder="username", id="username")
            yield Label("Password", classes="login-label")
            yield Input(placeholder="••••••••", password=True, id="password")
            yield Button("Login", id="login-btn", variant="primary")
            yield Label("", id="login-error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "login-btn":
            self._do_login()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "username":
            self.query_one("#password", Input).focus()
        elif event.input.id == "password":
            self._do_login()

    @work(exclusive=True)
    async def _do_login(self) -> None:
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        error_label = self.query_one("#login-error", Label)

        if not username or not password:
            error_label.update("Username and password are required.")
            error_label.add_class("visible")
            return

        error_label.remove_class("visible")
        btn = self.query_one("#login-btn", Button)
        btn.disabled = True
        btn.label = "Logging in…"

        client = VIgilusClient(base_url=self.base_url)
        try:
            resp = await client.login(username, password)
            save_token(resp["token"], resp["expires_at"], resp["username"], self.base_url)
            client.set_token(resp["token"])
            self.post_message(self.LoggedIn(client, resp["username"]))
        except ApiError as e:
            error_label.update(str(e))
            error_label.add_class("visible")
            btn.disabled = False
            btn.label = "Login"


# ── Provider wizard screen ────────────────────────────────────────────────────


class ProviderWizardScreen(Screen[dict | None]):
    """Guided provider setup — stepped prompts, mirrors the web ProviderWizard."""

    BINDINGS = [
        Binding("ctrl+c", "dismiss(None)", "Cancel"),
        Binding("escape", "dismiss(None)", "Cancel"),
    ]
    DEFAULT_CSS = """
    ProviderWizardScreen {
        align: center middle;
    }
    #wizard-box {
        width: 60;
        height: auto;
        border: solid $primary;
        padding: 1 2;
    }
    #wizard-title {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    .wiz-label {
        color: $text-muted;
        margin-top: 1;
    }
    #wiz-error {
        color: $error;
        margin-top: 1;
        display: none;
    }
    #wiz-error.visible { display: block; }
    #wiz-status {
        color: $success;
        margin-top: 1;
        display: none;
    }
    #wiz-status.visible { display: block; }
    #catalog-list {
        height: auto;
        max-height: 10;
        border: solid $surface;
        margin-top: 1;
    }
    #wiz-btn-row {
        margin-top: 1;
    }
    """

    def __init__(self, client: VIgilusClient, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._client = client
        self._catalog: list[dict] = []
        self._selected: dict | None = None
        self._created_id: str | None = None

    def compose(self) -> ComposeResult:
        with Container(id="wizard-box"):
            yield Label("◈ Add AI Provider  (/login)", id="wizard-title")
            yield Label("Loading providers…", id="wiz-intro")
            yield OptionList(id="catalog-list")
            yield Label("Display name:", classes="wiz-label")
            yield Input(placeholder="e.g. My Anthropic", id="wiz-name")
            yield Label("API Key (leave blank if not needed):", classes="wiz-label")
            yield Input(placeholder="sk-ant-…", password=True, id="wiz-key")
            yield Label("Base URL (leave blank for default):", classes="wiz-label")
            yield Input(placeholder="http://localhost:11434/v1", id="wiz-url")
            yield Label("", id="wiz-error")
            yield Label("", id="wiz-status")
            with Horizontal(id="wiz-btn-row"):
                yield Button("Add Provider", id="wiz-add", variant="primary")
                yield Button("Cancel", id="wiz-cancel")

    def on_mount(self) -> None:
        self._load_catalog()

    @work(exclusive=True)
    async def _load_catalog(self) -> None:
        try:
            self._catalog = await self._client.get_provider_catalog()
            ol = self.query_one("#catalog-list", OptionList)
            ol.clear_options()
            for entry in self._catalog:
                label = f"{entry['label']}  [{entry.get('default_model') or 'any model'}]"
                ol.add_option(Option(label, id=entry["id"]))
            if self._catalog:
                ol.highlighted = 0
                self._select_catalog(self._catalog[0])
            self.query_one("#wiz-intro", Label).update("Select a provider:")
        except ApiError as e:
            self.query_one("#wiz-intro", Label).update(f"[red]Could not load catalog: {e}[/red]")

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        entry = next((e for e in self._catalog if e["id"] == event.option_id), None)
        if entry:
            self._select_catalog(entry)

    def _select_catalog(self, entry: dict) -> None:
        self._selected = entry
        self.query_one("#wiz-name", Input).value = entry["label"]
        self.query_one("#wiz-url", Input).value = entry.get("base_url") or ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "wiz-cancel":
            self.dismiss(None)
        elif event.button.id == "wiz-add":
            self._do_add()

    @work(exclusive=True)
    async def _do_add(self) -> None:
        selected = self._selected
        if not selected:
            self._show_error("Select a provider from the list first.")
            return

        name = self.query_one("#wiz-name", Input).value.strip() or selected["label"]
        api_key = self.query_one("#wiz-key", Input).value.strip() or None
        base_url = self.query_one("#wiz-url", Input).value.strip() or None

        if selected.get("needs_api_key") and not api_key:
            self._show_error(f"{selected['label']} requires an API key.")
            return

        btn = self.query_one("#wiz-add", Button)
        btn.disabled = True
        btn.label = "Creating…"

        try:
            provider = await self._client.create_provider(
                {
                    "name": name,
                    "type": selected["type"],
                    "api_key": api_key,
                    "base_url": base_url or selected.get("base_url"),
                    "default_model": selected.get("default_model"),
                    "enabled": True,
                }
            )
            self._created_id = provider["id"]
        except ApiError as e:
            self._show_error(str(e))
            btn.disabled = False
            btn.label = "Add Provider"
            return

        # Test connection
        btn.label = "Testing…"
        try:
            result = await self._client.test_provider(self._created_id)
            if result.get("ok"):
                models = result.get("models", [])
                best_model = models[0] if models else selected.get("default_model")
                if best_model:
                    await self._client.update_provider(
                        self._created_id, {"default_model": best_model}
                    )
                self._show_status(f"✓ Connected! Using model: {best_model or 'default'}")
            else:
                self._show_status(
                    f"⚠ Provider saved but test failed: {result.get('error', 'unknown')}"
                )
        except ApiError as e:
            self._show_status(f"⚠ Provider saved but test failed: {e}")

        # Set as orchestrator default
        try:
            await self._client.update_orchestrator_config({"provider_id": self._created_id})
        except ApiError:
            pass

        await asyncio.sleep(1.5)
        self.dismiss({"id": self._created_id, "name": name})

    def _show_error(self, msg: str) -> None:
        lbl = self.query_one("#wiz-error", Label)
        lbl.update(msg)
        lbl.add_class("visible")

    def _show_status(self, msg: str) -> None:
        lbl = self.query_one("#wiz-status", Label)
        lbl.update(msg)
        lbl.add_class("visible")


# ── Chat screen ───────────────────────────────────────────────────────────────


class Transcript(ScrollableContainer):
    """Scrollable message log."""

    DEFAULT_CSS = """
    Transcript {
        background: $surface;
        padding: 0 1;
    }
    .msg-user {
        color: $text;
        background: $primary-darken-2;
        padding: 0 1;
        margin: 0 0 1 4;
        border-left: solid $primary;
    }
    .msg-assistant {
        color: $text;
        padding: 0 1;
        margin: 0 4 1 0;
        border-left: solid $secondary;
    }
    .msg-system {
        color: $text-muted;
        text-style: italic;
        padding: 0 1;
        margin: 0 0 1 0;
        border-left: solid $surface-lighten-3;
    }
    .msg-error {
        color: $error;
        padding: 0 1;
        margin: 0 0 1 0;
        border-left: solid $error;
    }
    """

    def add_message(self, msg: ChatMessage) -> Static:
        css_class = f"msg-{msg.role}"
        prefix = {"user": "You", "assistant": "Vigilus", "system": "◈", "error": "✗"}.get(
            msg.role, msg.role
        )
        widget = Static(f"[bold]{prefix}:[/bold] {msg.text}", classes=css_class)
        self.mount(widget)
        self.scroll_end(animate=False)
        return widget

    def append_to_last(self, text: str) -> None:
        children = list(self.children)
        if not children:
            return
        last = children[-1]
        if isinstance(last, Static) and "msg-assistant" in last.classes:
            current = str(last.renderable)
            last.update(current + text)
            self.scroll_end(animate=False)


class SessionItem(ListItem):
    def __init__(self, session_id: str, title: str) -> None:
        super().__init__(Label(title or "Chat Session"))
        self.session_id = session_id
        self._title = title


class ChatScreen(Screen[None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+n", "new_session", "New chat"),
        Binding("ctrl+l", "clear_transcript", "Clear"),
        Binding("ctrl+q", "logout", "Logout"),
        Binding("f1", "toggle_sessions", "Sessions"),
    ]
    DEFAULT_CSS = """
    ChatScreen {
        layout: vertical;
    }
    #body {
        layout: horizontal;
        height: 1fr;
    }
    #sessions-panel {
        width: 24;
        border-right: solid $surface-darken-2;
        background: $surface-darken-1;
        display: block;
    }
    #sessions-panel.hidden {
        display: none;
    }
    #sessions-header {
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
        text-style: bold;
    }
    #session-list {
        height: 1fr;
    }
    #content {
        layout: vertical;
        width: 1fr;
    }
    #transcript {
        height: 1fr;
    }
    #input-area {
        height: auto;
        border-top: solid $surface-darken-2;
        padding: 0;
    }
    #cmd-popup {
        background: $surface-darken-1;
        border: solid $primary;
        height: auto;
        max-height: 10;
        display: none;
    }
    #cmd-popup.visible {
        display: block;
    }
    #compose {
        height: 3;
        layout: horizontal;
    }
    #compose-prompt {
        width: 4;
        padding: 1 0 0 1;
        color: $primary;
        text-style: bold;
    }
    #compose-input {
        width: 1fr;
    }
    #status-bar {
        height: 1;
        background: $surface-darken-2;
        color: $text-muted;
        padding: 0 1;
        text-style: italic;
    }
    """

    # Posted when an SSE event arrives from the stream worker
    @dataclass
    class SseEvent(Message):
        event_type: str
        data: dict

    def __init__(self, client: VIgilusClient, username: str, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._client = client
        self._username = username
        self._sessions: list[dict] = []
        self._active_session: dict | None = None
        self._commands: list[dict] = []
        self._busy = False
        self._stream_task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="sessions-panel"):
                yield Label(" Sessions", id="sessions-header")
                yield ListView(id="session-list")
            with Vertical(id="content"):
                yield Transcript(id="transcript")
                with Vertical(id="input-area"):
                    yield OptionList(id="cmd-popup")
                    with Horizontal(id="compose"):
                        yield Label("> ", id="compose-prompt")
                        yield Input(
                            placeholder="Message Vigilus… (/ for commands, @ for operators)",
                            id="compose-input",
                        )
                    yield Label("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._load_data()

    @work(exclusive=True)
    async def _load_data(self) -> None:
        self._set_status("Loading…")
        try:
            self._sessions = await self._client.list_sessions()
            self._commands = await self._client.list_commands()
            self._rebuild_session_list()
            if self._sessions:
                await self._select_session(self._sessions[0])
            self._set_status(
                f"Logged in as {self._username}  ·  Ctrl+N new chat  ·  F1 sessions  ·  /help for commands"
            )
        except ApiError as e:
            self._set_status(f"[red]Load error: {e}[/red]")

    def _rebuild_session_list(self) -> None:
        lv = self.query_one("#session-list", ListView)
        lv.clear()
        for sess in self._sessions:
            lv.append(SessionItem(sess["id"], sess.get("title") or "Chat Session"))

    async def _select_session(self, sess: dict) -> None:
        self._active_session = sess
        transcript = self.query_one("#transcript", Transcript)
        transcript.remove_children()
        try:
            messages = await self._client.list_messages(sess["id"])
            for msg in messages:
                content = msg.get("content", "")
                if isinstance(content, dict):
                    content = content.get("text") or str(content)
                role = msg.get("role", "assistant")
                if role not in ("user", "assistant"):
                    role = "assistant"
                transcript.add_message(ChatMessage(role=role, text=content))
        except ApiError as e:
            transcript.add_message(ChatMessage(role="error", text=str(e)))
        self.query_one("#compose-input", Input).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SessionItem):
            sess = next((s for s in self._sessions if s["id"] == event.item.session_id), None)
            if sess:
                self.run_worker(self._select_session(sess), exclusive=True)

    # ── Input handling ─────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "compose-input":
            return
        self._update_cmd_popup(event.value)

    def _update_cmd_popup(self, value: str) -> None:
        popup = self.query_one("#cmd-popup", OptionList)
        if not value.startswith("/") or " " in value:
            popup.remove_class("visible")
            return
        query = value[1:].lower()
        matches = [
            c
            for c in self._commands
            if c["name"].startswith(query) or query in c["summary"].lower()
        ][:8]
        if not matches:
            popup.remove_class("visible")
            return
        popup.clear_options()
        for cmd in matches:
            label = f"/{cmd['name']}  {cmd['summary']}"
            popup.add_option(Option(label, id=cmd["name"]))
        popup.add_class("visible")
        if matches:
            popup.highlighted = 0

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "compose-input":
            return
        popup = self.query_one("#cmd-popup", OptionList)
        if "visible" in popup.classes and self._complete_from_popup(popup, event.input):
            # Enter accepted the highlighted command instead of sending.
            return
        self._send_input(event.input.value)

    def on_key(self, event) -> None:
        inp = self.query_one("#compose-input", Input)
        if self.focused is not inp:
            return
        popup = self.query_one("#cmd-popup", OptionList)
        if "visible" not in popup.classes:
            return
        count = popup.option_count
        if count == 0:
            return
        if event.key == "escape":
            popup.remove_class("visible")
            event.stop()
        elif event.key in ("tab", "enter"):
            # Complete from the popup. (Enter is also handled via Input.Submitted,
            # but stopping it here keeps focus and prevents a stray newline.)
            self._complete_from_popup(popup, inp)
            event.stop()
        elif event.key == "up":
            hi = popup.highlighted or 0
            popup.highlighted = (hi - 1) % count  # wrap, like the web @ popup
            event.stop()
        elif event.key == "down":
            hi = popup.highlighted or 0
            popup.highlighted = (hi + 1) % count
            event.stop()

    def _complete_from_popup(self, popup: OptionList, inp: Input) -> bool:
        """Insert the highlighted command into the input. Returns True if it did."""
        idx = popup.highlighted
        if idx is None or not (0 <= idx < popup.option_count):
            return False
        cmd_name = popup.get_option_at_index(idx).id
        inp.value = f"/{cmd_name} "
        inp.cursor_position = len(inp.value)
        popup.remove_class("visible")
        return True

    def _send_input(self, raw: str) -> None:
        text = raw.strip()
        if not text or self._busy:
            return
        self.query_one("#compose-input", Input).value = ""
        self.query_one("#cmd-popup", OptionList).remove_class("visible")

        if text.startswith("/"):
            parts = text[1:].split(None, 1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            self._dispatch_command(cmd, args)
            return

        if not self._active_session:
            self._sys_notice("No active session. Use /new to create one.")
            return
        self._do_send(text)

    @work(exclusive=False)
    async def _dispatch_command(self, cmd: str, args: str) -> None:
        # Client-side commands
        if cmd == "clear":
            self.query_one("#transcript", Transcript).remove_children()
            return
        if cmd == "logout":
            clear_token()
            self.app.pop_screen()
            return
        if cmd == "quit":
            self.app.exit()
            return
        if cmd == "login":
            result = await self.app.push_screen_wait(ProviderWizardScreen(self._client))
            if result:
                self._sys_notice(f"Provider **{result['name']}** added and set as default.")
            return

        # Server-side commands
        try:
            result = await self._client.run_command(
                cmd, args, self._active_session["id"] if self._active_session else None
            )
        except ApiError as e:
            self._sys_notice(str(e), is_error=True)
            return

        kind = result.get("kind", "markdown")
        text = result.get("text", "")
        data = result.get("data") or {}

        if kind == "error":
            self._sys_notice(text, is_error=True)
        elif kind == "markdown":
            self._sys_notice(text)
        elif kind == "session_created":
            sess = data.get("session", {})
            if sess:
                self._sessions.insert(0, sess)
                self._rebuild_session_list()
                await self._select_session(sess)
            if text:
                self._sys_notice(text)
        elif kind == "session_switch":
            sess = data.get("session", {})
            if sess:
                existing = next((s for s in self._sessions if s["id"] == sess["id"]), None)
                await self._select_session(existing or sess)
        elif kind == "session_deleted":
            sid = data.get("session_id")
            if sid:
                self._sessions = [s for s in self._sessions if s["id"] != sid]
                self._rebuild_session_list()
                if self._active_session and self._active_session["id"] == sid:
                    self._active_session = None
                    self.query_one("#transcript", Transcript).remove_children()
                    if self._sessions:
                        await self._select_session(self._sessions[0])
            if text:
                self._sys_notice(text)
        elif kind == "config_changed":
            if text:
                self._sys_notice(text)
        elif kind == "stopped":
            self._busy = False
            if text:
                self._sys_notice(text)

    @work(exclusive=False)
    async def _do_send(self, text: str) -> None:
        if not self._active_session:
            return
        self._busy = True
        session_id = self._active_session["id"]
        transcript = self.query_one("#transcript", Transcript)

        transcript.add_message(ChatMessage(role="user", text=text))
        self._set_status("Thinking…")

        # Start streaming listener BEFORE posting the message
        streaming_widget: Static | None = None
        stream_done = asyncio.Event()

        async def _stream() -> None:
            nonlocal streaming_widget
            try:
                async for event in self._client.stream_session(session_id):
                    etype = event.get("type", "")
                    edata = event.get("data", {})
                    if etype == "text_delta":
                        delta = edata.get("text", "")
                        if delta:
                            if streaming_widget is None:
                                streaming_widget = transcript.add_message(
                                    ChatMessage(role="assistant", text="")
                                )
                            transcript.append_to_last(delta)
                    elif etype == "thinking":
                        self._set_status(f"Thinking… (iteration {edata.get('iteration', '?')})")
                    elif etype == "tool_call":
                        self._set_status(f"Running {edata.get('tool', 'tool')}…")
                    elif etype == "delegation_start":
                        self._set_status(f"Delegating to {edata.get('operator', 'operator')}…")
                    elif etype == "done":
                        break
                    elif etype == "error":
                        err = edata.get("error", "Unknown error")
                        transcript.add_message(ChatMessage(role="error", text=err))
                        break
            except Exception as e:
                transcript.add_message(ChatMessage(role="error", text=f"Stream error: {e}"))
            finally:
                stream_done.set()

        stream_task = asyncio.create_task(_stream())

        try:
            await self._client.send_message(session_id, text)
        except ApiError as e:
            stream_task.cancel()
            transcript.add_message(ChatMessage(role="error", text=str(e)))
            self._busy = False
            self._set_status(self._idle_status())
            return

        await stream_done.wait()
        stream_task.cancel()

        # Reload messages for the final saved state
        try:
            messages = await self._client.list_messages(session_id)
            if messages:
                last = messages[-1]
                if last.get("role") == "assistant" and streaming_widget is None:
                    content = last.get("content", "")
                    if isinstance(content, dict):
                        content = content.get("text", str(content))
                    transcript.add_message(ChatMessage(role="assistant", text=content))
            # Refresh session title
            self._sessions = await self._client.list_sessions()
            self._rebuild_session_list()
        except ApiError:
            pass

        self._busy = False
        self._set_status(self._idle_status())

    # ── Action helpers ─────────────────────────────────────────────────────────

    def action_new_session(self) -> None:
        self.run_worker(self._create_session(), exclusive=False)

    async def _create_session(self) -> None:
        try:
            sess = await self._client.create_session("")
            self._sessions.insert(0, sess)
            self._rebuild_session_list()
            await self._select_session(sess)
        except ApiError as e:
            self._sys_notice(str(e), is_error=True)

    def action_clear_transcript(self) -> None:
        self.query_one("#transcript", Transcript).remove_children()

    def action_logout(self) -> None:
        clear_token()
        self.app.pop_screen()

    def action_toggle_sessions(self) -> None:
        panel = self.query_one("#sessions-panel")
        panel.toggle_class("hidden")

    def _sys_notice(self, text: str, is_error: bool = False) -> None:
        role = "error" if is_error else "system"
        transcript = self.query_one("#transcript", Transcript)
        transcript.add_message(ChatMessage(role=role, text=text))

    def _set_status(self, text: str) -> None:
        self.query_one("#status-bar", Label).update(text)

    def _idle_status(self) -> str:
        name = (
            self._active_session.get("title") or "Chat Session"
            if self._active_session
            else "no session"
        )
        return f"Session: {name}  ·  Ctrl+N new  ·  F1 sessions  ·  /help"


# ── Main App ───────────────────────────────────────────────────────────────────


class VIgilusTUI(App[None]):
    """Vigilus terminal UI."""

    TITLE = "Vigilus"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    def __init__(self, base_url: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._base_url = base_url or get_base_url()

    def on_mount(self) -> None:
        token = get_token()
        if token:
            client = VIgilusClient(base_url=self._base_url, token=token)
            username = get_username() or "user"
            self.push_screen(ChatScreen(client, username))
        else:
            self.push_screen(LoginScreen(self._base_url))

    def on_login_screen_logged_in(self, event: LoginScreen.LoggedIn) -> None:
        self.pop_screen()
        self.push_screen(ChatScreen(event.client, event.username))


def launch(base_url: str | None = None) -> None:
    """Entry point called by ``vigilus chat``."""
    app = VIgilusTUI(base_url=base_url)
    app.run()
