"""opencode-agent-tui — Terminal agent configuration editor for OpenCode."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll, Grid
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button, DataTable, Footer, Header, Input, Label, Static,
    Select, Switch, TabbedContent, TabPane, TextArea, RichLog,
)
from textual.widgets.data_table import RowKey

from .config import (
    AgentDef, GLOBAL_CONFIG_DIR, discover_all_agents, get_collision_summary,
    load_all_configs, find_all_configs, KNOWN_AGENT_FIELDS, _parse_frontmatter,
)
from .permissions import (
    Action, Rule, Ruleset, from_config, evaluate, disabled, compute_provenience,
    Provenience, Source,
)
from .tool_discovery import build_tool_catalog, ToolCatalog, get_tool_display_name
from .config_writer import (
    SaveResult, save_agent, snapshot_file, check_stale,
    ConcurrencyCheck, _atomic_write,
    write_markdown_agent, write_json_agent_permission,
)
from .backup import backup_config


# ═══════════════════════════════════════════════════════════════════════════
# State
# ═══════════════════════════════════════════════════════════════════════════

class AppState:
    def __init__(self) -> None:
        self.config: dict = {}
        self.agents: list[AgentDef] = []
        self.sources: dict[str, Path] = {}
        self.warnings: list[str] = []
        self.catalog: ToolCatalog | None = None
        self.selected_agent: AgentDef | None = None
        self.snapshots: dict[str, ConcurrencyCheck | None] = {}

    def reload(self) -> None:
        self.config = load_all_configs()
        self.sources = find_all_configs()
        self.agents = discover_all_agents(self.config)
        self.warnings = get_collision_summary(self.agents)
        self.catalog = build_tool_catalog(self.config.get("mcp", {}), force_refresh=True)
        for path in self.sources.values():
            self.snapshots[str(path)] = snapshot_file(path)

    def get_agent_rulesets(self, agent: AgentDef) -> tuple[Ruleset, Ruleset, Ruleset]:
        """Return (builtin, global, agent) rulesets for an agent."""
        builtin = from_config(agent.permission) if agent.permission else []
        global_perms = self.config.get("permission", {})
        global_rules = from_config(global_perms) if isinstance(global_perms, dict) else []
        agent_perms = {}  # agent-level is already in builtin for markdown agents
        agent_rules = from_config(agent_perms)
        return builtin, global_rules, agent_rules


STATE = AppState()

# ═══════════════════════════════════════════════════════════════════════════
# Color helpers for actions
# ═══════════════════════════════════════════════════════════════════════════

ACTION_ICON = {
    Action.ALLOW: "🟢",
    Action.ASK: "🟡",
    Action.DENY: "🔴",
}

ACTION_STYLE = {
    Action.ALLOW: "bold green",
    Action.ASK: "bold yellow",
    Action.DENY: "bold red",
}

BUILTIN_TOOL_CATEGORIES = {
    "files": ("read", "edit", "write", "glob", "grep", "lsp"),
    "execution": ("bash", "task"),
    "interaction": ("question", "todowrite", "skill"),
    "web": ("webfetch", "websearch"),
    "mcp_meta": ("list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource"),
}

COMBINER_SERVER_ORDER = ["github", "git-repo", "sequential-thinking", "time", "combiner"]


# ═══════════════════════════════════════════════════════════════════════════
# Main Screen — Agent List
# ═══════════════════════════════════════════════════════════════════════════

class AgentListTable(DataTable):
    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True


class MainScreen(Screen):
    BINDINGS = [
        Binding("enter", "select", "Edit"),
        Binding("q", "quit", "Quit"),
        Binding("/", "focus_search", "Search"),
        Binding("r", "reload", "Reload"),
        Binding("h", "toggle_shadowed", "Shadowed"),
        Binding("m", "cycle_mode", "Mode"),
        Binding("n", "new_agent", "New"),
        Binding("c", "clone_agent", "Clone"),
        Binding("delete", "delete_agent", "Delete"),
        Binding("backspace", "backup_screen", "Backups"),
        Binding("?", "help_screen", "Help"),
    ]

    _filter: str = ""
    _show_shadowed: bool = False
    _mode_filter: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main"):
            with Horizontal(id="toolbar"):
                yield Input(placeholder="Search agents...", id="search")
                yield Button("Reload", id="reload_btn", variant="primary")
            yield Static(id="info")
            with Container(id="shadow_banner_box"):
                yield Static("", id="shadow_banner")
            with Horizontal(id="body"):
                yield AgentListTable(id="agents")
                with Container(id="preview"):
                    yield Static(id="preview_text", markup=False)
                    with Horizontal(id="preview_actions"):
                        yield Button("Edit", id="edit_btn", variant="primary")
                        yield Button("Clone", id="clone_btn")
                        yield Button("Delete", id="del_btn", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        STATE.reload()
        self._check_shadows()
        self._build()

    def _check_shadows(self) -> None:
        """Run a fast shadow audit and show ⚠ banner if shadowed agents exist."""
        from .audit import run_audit
        try:
            audit_text = run_audit("text")
            shadowed_count = sum(1 for a in STATE.agents if a.shadowed and a.source_type == "inline")
            if shadowed_count > 0:
                banner = self.query_one("#shadow_banner", Static)
                lines = audit_text.split("\n")[:4]
                banner.update("\n".join(lines))
                self.query_one("#shadow_banner_box").display = True
            else:
                self.query_one("#shadow_banner_box").display = False
        except Exception:
            self.query_one("#shadow_banner_box").display = False

    def _build(self) -> None:
        table = self.query_one("#agents", AgentListTable)
        table.clear(columns=True)
        table.add_columns("Name", "Mode", "Model", "Temp", "Source")

        agents = self._filtered()
        for a in agents:
            style = ""
            if a.shadowed:
                style = "dim"
            if a.disabled:
                style = "dim italic"
            table.add_row(
                a.name, a.mode, str(a.model or ""),
                f"{a.temperature:.1f}" if a.temperature is not None else "",
                a.source_type, key=a.name,
            )

        parts = [f"{len(agents)}/{len(STATE.agents)}"]
        if self._filter:
            parts.append(f'"{self._filter}"')
        parts.append(self._mode_filter or "all")
        parts.append("visible" if not self._show_shadowed else "+shadowed")
        n_warn = len(STATE.warnings)
        if n_warn:
            parts.append(f"⚠ {n_warn}")
        self.query_one("#info", Static).update(" │ ".join(parts))
        table.focus()
        self._update_preview()

    def _filtered(self) -> list[AgentDef]:
        agents = STATE.agents
        if self._filter:
            t = self._filter.lower()
            agents = [a for a in agents if t in a.name.lower()
                      or (a.description and t in a.description.lower())]
        if not self._show_shadowed:
            agents = [a for a in agents if not a.shadowed]
        if self._mode_filter:
            agents = [a for a in agents if a.mode == self._mode_filter]
        return agents

    def _update_preview(self) -> None:
        table = self.query_one("#agents", AgentListTable)
        preview = self.query_one("#preview_text", Static)
        if table.row_count == 0:
            preview.update("")
            return
        key = table.coordinate_to_cell_key(table.cursor_coordinate)
        if key is None:
            return
        name = str(key.row_key.value) if key.row_key.value else ""
        agent = next((a for a in STATE.agents if a.name == name), None)
        if agent is None:
            preview.update("")
            return
        lines = [
            f"  {agent.name}",
            f"  Mode: {agent.mode}  │  Model: {agent.model or '(default)'}",
            f"  Temp: {agent.temperature}  │  Color: {agent.color or '-'}",
            f"  Source: {agent.source_type}",
            f"  Permissions: {len(agent.permission)} rules",
            f"  {agent.description or '(no description)'}",
        ]
        if agent.shadowed:
            lines.append("  ⚠ SHADOWED — markdown definition wins.")
        if agent.prompt:
            lines.append(f"  Prompt: {len(agent.prompt)} chars")
        preview.update("\n".join(lines))

    @on(DataTable.RowHighlighted)
    def _on_highlight(self) -> None:
        self._update_preview()

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    @on(Input.Changed)
    def _on_search(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self._filter = event.value
            self._build()

    def action_reload(self) -> None:
        STATE.reload()
        self._build()
        self.notify("Reloaded", timeout=2)

    def action_toggle_shadowed(self) -> None:
        self._show_shadowed = not self._show_shadowed
        self._build()

    def action_cycle_mode(self) -> None:
        modes = [None, "primary", "subagent"]
        i = modes.index(self._mode_filter) if self._mode_filter in modes else 0
        self._mode_filter = modes[(i + 1) % len(modes)]
        self._build()

    def _get_selected_agent(self) -> AgentDef | None:
        table = self.query_one("#agents", AgentListTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate)
        if key is None:
            return None
        name = str(key.row_key.value) if key.row_key.value else ""
        return next((a for a in STATE.agents if a.name == name), None)

    def action_select(self) -> None:
        agent = self._get_selected_agent()
        if agent is None:
            return
        if agent.shadowed:
            self.notify(f"⚠ '{agent.name}' is shadowed — edits have zero effect", severity="warning", timeout=6)
            return
        STATE.selected_agent = agent
        self.app.push_screen(DetailScreen(agent))

    def action_new_agent(self) -> None:
        self.app.push_screen(NewAgentScreen())

    def action_clone_agent(self) -> None:
        agent = self._get_selected_agent()
        if agent is None:
            return
        self.app.push_screen(NewAgentScreen(template=agent))

    def action_delete_agent(self) -> None:
        agent = self._get_selected_agent()
        if agent is None:
            return
        self.app.push_screen(ConfirmScreen(
            f"Delete agent '{agent.name}'?",
            lambda: self._do_delete(agent),
        ))

    def _do_delete(self, agent: AgentDef) -> None:
        cfg = STATE.config
        agents = cfg.get("agent", {})
        if agent.name in agents:
            backup_before = str(list(STATE.sources.values())[0]) if STATE.sources else None
            if backup_before:
                backup_config(Path(backup_before))
            del agents[agent.name]
            target = list(STATE.sources.values())[0] if STATE.sources else None
            if target:
                _atomic_write(target, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
            self.notify(f"Deleted '{agent.name}' from JSON config", timeout=4)
        else:
            self.notify(f"'{agent.name}' is markdown-defined. Delete the .md file manually.", severity="warning")
        STATE.reload()
        self._build()

    def action_backup_screen(self) -> None:
        self.app.push_screen(BackupScreen())

    def action_help_screen(self) -> None:
        self.app.push_screen(HelpScreen())

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "reload_btn":
            self.action_reload()
        elif bid == "edit_btn":
            self.action_select()
        elif bid == "clone_btn":
            self.action_clone_agent()
        elif bid == "del_btn":
            self.action_delete_agent()


# ═══════════════════════════════════════════════════════════════════════════
# New Agent Screen
# ═══════════════════════════════════════════════════════════════════════════

class NewAgentScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Cancel"),
        Binding("ctrl+s", "create", "Create"),
    ]

    def __init__(self, template: AgentDef | None = None) -> None:
        super().__init__()
        self._tmpl = template

    def compose(self) -> ComposeResult:
        t = self._tmpl
        title = f"Clone: {t.name}" if t else "Create Agent"
        yield Header(show_clock=True)
        yield Static(title, id="new_title")
        with Container(id="new_form"):
            with Horizontal():
                yield Label("Name:")
                yield Input(value="", id="new_name", placeholder="my-agent")
            with Horizontal():
                yield Label("Mode:")
                yield Select([("subagent", "subagent"), ("primary", "primary"), ("all", "all")],
                             value=t.mode if t else "subagent", id="new_mode")
            with Horizontal():
                yield Label("Model:")
                yield Input(value=str(t.model or ""), id="new_model", placeholder="provider/model")
            with Horizontal():
                yield Label("Temp:")
                yield Input(value=str(t.temperature or "0.1"), id="new_temp")
            with Horizontal():
                yield Label("Make markdown file?")
                yield Switch(value=True, id="new_as_md")
            with Horizontal():
                yield Label("Description:")
                yield Input(value=str(t.description or ""), id="new_desc", placeholder="What this agent does")
            with Horizontal(id="new_actions"):
                yield Button("Create", id="create_btn", variant="primary")
                yield Button("Cancel", id="cancel_btn")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_create(self) -> None:
        self._do_create()

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        if event.button.id == "create_btn":
            self._do_create()
        elif event.button.id == "cancel_btn":
            self.app.pop_screen()

    def _do_create(self) -> None:
        name = self.query_one("#new_name", Input).value.strip()
        if not name:
            self.notify("Name is required", severity="error")
            return
        mode = self.query_one("#new_mode", Select).value
        model = self.query_one("#new_model", Input).value.strip() or None
        temp_s = self.query_one("#new_temp", Input).value.strip()
        try:
            temp = float(temp_s) if temp_s else 0.1
        except ValueError:
            self.notify("Invalid temperature", severity="error")
            return
        desc = self.query_one("#new_desc", Input).value.strip() or None
        as_md = self.query_one("#new_as_md", Switch).value

        cfg = dict(STATE.config)
        if "agent" not in cfg:
            cfg["agent"] = {}

        if as_md:
            # Create markdown file
            import yaml
            fm = {"mode": mode}
            if model:
                fm["model"] = model
            fm["temperature"] = temp
            if desc:
                fm["description"] = desc
            yaml_body = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
            md_content = f"---\n{yaml_body}---\n\n"
            if self._tmpl and self._tmpl.prompt:
                md_content += self._tmpl.prompt
            target_dir = GLOBAL_CONFIG_DIR / "agents"
            target_dir.mkdir(parents=True, exist_ok=True)
            filepath = target_dir / f"{name}.md"
            _atomic_write(filepath, md_content)
            self.notify(f"Created markdown agent: {filepath}", timeout=4)
        else:
            agent_def = {"mode": mode, "temperature": temp}
            if model:
                agent_def["model"] = model
            if desc:
                agent_def["description"] = desc
            # Copy permissions from template if cloning
            if self._tmpl and self._tmpl.permission:
                agent_def["permission"] = dict(self._tmpl.permission)
            cfg["agent"][name] = agent_def
            target = list(STATE.sources.values())[0] if STATE.sources else None
            if target:
                backup_config(target)
                _atomic_write(target, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
                self.notify(f"Created inline agent: {name}", timeout=4)

        STATE.reload()
        self.app.pop_screen()


# ═══════════════════════════════════════════════════════════════════════════
# Confirm Screen
# ═══════════════════════════════════════════════════════════════════════════

class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("y", "confirm", "Yes"), Binding("n", "cancel", "No"), Binding("escape", "cancel", "Cancel")]

    def __init__(self, message: str, on_yes) -> None:
        super().__init__()
        self._msg = message
        self._on_yes = on_yes

    def compose(self) -> ComposeResult:
        yield Container(
            Static(f"\n  {self._msg}\n\n  Are you sure?", id="confirm_msg"),
            Horizontal(
                Button("Yes (y)", id="yes_btn", variant="error"),
                Button("No (n)", id="no_btn"),
                id="confirm_btns",
            ),
            id="confirm_box",
        )

    def action_confirm(self) -> None:
        self._on_yes()
        self.app.pop_screen()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        if event.button.id == "yes_btn":
            self.action_confirm()
        elif event.button.id == "no_btn":
            self.action_cancel()


# ═══════════════════════════════════════════════════════════════════════════
# Detail Screen — Agent editor
# ═══════════════════════════════════════════════════════════════════════════

class DetailScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("ctrl+s", "save", "Save"),
        Binding("left", "prev_tab", "Prev tab"),
        Binding("right", "next_tab", "Next tab"),
        Binding("e", "export_md", "Export .md"),
    ]

    def __init__(self, agent: AgentDef) -> None:
        super().__init__()
        self.agent = agent

    def compose(self) -> ComposeResult:
        a = self.agent
        yield Header(show_clock=True)
        with Container(id="detail"):
            yield Static(f"Editing: {a.name}  [{a.source_type}]  {a.source_path or ''}", id="detail_title")
            with TabbedContent(id="tabs"):
                with TabPane(" Config ", id="tab_config"):
                    yield self._config_pane()
                with TabPane(" Permissions ", id="tab_perms"):
                    yield self._permissions_pane()
                with TabPane(" MCP Tools ", id="tab_mcp"):
                    yield self._mcp_tools_pane()
                with TabPane(" Prompt ", id="tab_prompt"):
                    yield self._prompt_pane()
                with TabPane(" Raw ", id="tab_raw"):
                    yield self._raw_pane()
        yield Footer()

    # ── Config pane ──────────────────────────────────────────────────────

    def _config_pane(self) -> Container:
        a = self.agent
        return Container(
            Horizontal(
                Vertical(Label("Model:"), Label("Variant:"), Label("Temp:"), Label("Mode:"),
                         Label("Color:"), Label("Steps:"), classes="labels"),
                Vertical(
                    Input(value=str(a.model or ""), id="cfg_model", placeholder="provider/model"),
                    Input(value=str(a.variant or ""), id="cfg_variant"),
                    Input(value=str(a.temperature or ""), id="cfg_temp"),
                    Select([(m, m) for m in ("primary", "subagent", "all")], value=a.mode, id="cfg_mode"),
                    Input(value=str(a.color or ""), id="cfg_color", placeholder="#RRGGBB"),
                    Input(value=str(a.steps or ""), id="cfg_steps"),
                    classes="fields",
                ),
                Vertical(
                    Switch(value=not a.disabled, id="cfg_enabled"), Label("Enabled"),
                    Switch(value=a.hidden, id="cfg_hidden"), Label("Hidden"),
                    id="switches",
                ),
                id="cfg_row",
            ),
            TextArea(text=a.description or "", id="cfg_desc"),
            id="config_pane",
        )

    # ── Permissions pane ─────────────────────────────────────────────────

    def _permissions_pane(self) -> Container:
        return Container(
            Horizontal(
                Select([
                    ("builtin", "Built-in"),
                    ("combiner", "Combiner"),
                    ("local", "Local MCP"),
                    ("browser", "Browser"),
                ], value="builtin", id="perm_group"),
                Static("", id="perm_info"),
                Button("+ Add Pattern", id="perm_add", variant="primary"),
                id="perm_toolbar",
            ),
            DataTable(id="perm_table"),
            id="perms_pane",
        )

    def _build_permissions(self) -> None:
        table = self.query_one("#perm_table", DataTable)
        table.clear(columns=True)
        table.cursor_type = "row"
        table.add_columns("Tool", "Action", "Provenience", "Pattern")

        group = self.query_one("#perm_group", Select).value
        builtin, global_rules, agent_rules = STATE.get_agent_rulesets(self.agent)

        tools: list[tuple[str, str]] = []

        if group == "builtin":
            for cat, names in BUILTIN_TOOL_CATEGORIES.items():
                for n in names:
                    tools.append((n, cat))
        elif group == "combiner":
            if STATE.catalog and STATE.catalog.combiner:
                tools = [(n, "combiner") for n in sorted(STATE.catalog.combiner)]
        elif group == "local":
            if STATE.catalog and STATE.catalog.local:
                tools = [(n, "local") for n in sorted(STATE.catalog.local)]
        elif group == "browser":
            if STATE.catalog and STATE.catalog.browser:
                tools = [(n, "browser") for n in sorted(STATE.catalog.browser)]

        # Limit display for performance
        display_limit = 100
        count = 0
        for tool_name, cat in tools:
            prov = compute_provenience(tool_name, "*", builtin, global_rules, agent_rules)
            action_icon = ACTION_ICON[prov.effective_action]
            src_label = "agent" if prov.is_overridden else prov.source_label
            table.add_row(
                tool_name,
                f"{action_icon} {prov.effective_action.value}",
                src_label,
                prov.effective_rule.pattern if prov.effective_rule else "*",
                key=tool_name,
            )
            count += 1
            if count >= display_limit:
                break

        info = self.query_one("#perm_info", Static)
        info.update(f"{count} tools │ ⬤=allow ⬤=ask ⬤=deny │ [Enter] toggle │ [*] wildcard │ [p] preview │ [+] add pattern")

    def _cycle_action(self, current: Action) -> Action:
        cycle = [Action.ALLOW, Action.ASK, Action.DENY]
        idx = cycle.index(current)
        return cycle[(idx + 1) % 3]

    def _toggle_permission(self) -> None:
        table = self.query_one("#perm_table", DataTable)
        if table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(table.cursor_coordinate)
        if key is None:
            return
        tool_name = str(key.row_key.value) if key.row_key.value else ""
        if not tool_name:
            return
        builtin, global_rules, agent_rules = STATE.get_agent_rulesets(self.agent)
        prov = compute_provenience(tool_name, "*", builtin, global_rules, agent_rules)
        current_action = prov.effective_action
        new_action = self._cycle_action(current_action)

        # Add/update agent-level override
        perms = dict(self.agent.permission)
        perms[tool_name] = new_action.value
        result = save_agent(self.agent, {"permission": perms}, STATE.sources)
        if result.success:
            self.notify(f"{tool_name}: {current_action.value} → {new_action.value}", timeout=3)
            STATE.reload()
            # Refresh agent reference
            self.agent = next((a for a in STATE.agents if a.name == self.agent.name), self.agent)
            self._build_permissions()
        else:
            self.notify(f"Save failed: {'; '.join(result.errors)}", severity="error")

    def _set_wildcard_permission(self) -> None:
        table = self.query_one("#perm_table", DataTable)
        if table.row_count == 0:
            return
        group = self.query_one("#perm_group", Select).value

        # Determine wildcard pattern based on group
        patterns: dict[str, str] = {
            "builtin": "*",
            "combiner": "mcp-combiner__*",
            "local": "codegraph*",
            "browser": "browser_*",
        }
        pattern = patterns.get(group, "*")

        builtin, global_rules, agent_rules = STATE.get_agent_rulesets(self.agent)
        prov = compute_provenience(pattern, "*", builtin, global_rules, agent_rules)
        new_action = self._cycle_action(prov.effective_action)

        perms = dict(self.agent.permission)
        perms[pattern] = new_action.value
        result = save_agent(self.agent, {"permission": perms}, STATE.sources)
        if result.success:
            self.notify(f"Wildcard {pattern}: → {new_action.value}", timeout=3)
            STATE.reload()
            self.agent = next((a for a in STATE.agents if a.name == self.agent.name), self.agent)
            self._build_permissions()
        else:
            self.notify(f"Save failed: {'; '.join(result.errors)}", severity="error")

    def _preview_rules(self) -> None:
        table = self.query_one("#perm_table", DataTable)
        if table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(table.cursor_coordinate)
        if key is None:
            return
        tool_name = str(key.row_key.value) if key.row_key.value else ""
        builtin, global_rules, agent_rules = STATE.get_agent_rulesets(self.agent)
        prov = compute_provenience(tool_name, "*", builtin, global_rules, agent_rules)

        lines = [
            f"Tool: {tool_name}",
            f"Effective: {prov.effective_action.value} (from {prov.source_label})",
            "",
            "Ruleset trace (findLast order, last match wins):",
        ]
        combined = builtin + global_rules + agent_rules
        from .wildcard import match as wm
        for i, r in enumerate(combined):
            marker = " ← WINNER" if r == prov.effective_rule else ""
            matches = wm(tool_name, r.permission)
            lines.append(f"  {i}: [{r.permission}] = {r.action.value}{marker}{' ✓' if matches else ''}")

        self.app.push_screen(MessageScreen("\n".join(lines)))

    @on(Select.Changed)
    def _on_group_change(self, event: Select.Changed) -> None:
        if event.select.id == "perm_group":
            self._build_permissions()

    @on(Button.Pressed)
    def _on_perm_button(self, event: Button.Pressed) -> None:
        if event.button.id == "perm_add":
            self.app.push_screen(AddPatternScreen(self.agent))

    @on(DataTable.RowSelected)
    def _on_perm_selected(self) -> None:
        self._toggle_permission()

    def on_mount(self) -> None:
        self._build_permissions()

    def _override_detail_bindings(self) -> None:
        pass  # Handled by DetailScreen bindings

    # ── MCP Tools pane ───────────────────────────────────────────────────

    def _mcp_tools_pane(self) -> Container:
        return Container(
            Select([], id="mcp_server"),
            Static("", id="mcp_tool_info"),
            DataTable(id="mcp_table"),
            id="mcp_pane",
        )

    def _build_mcp_tools(self) -> None:
        if STATE.catalog is None or not STATE.catalog.combiner:
            return
        tools = STATE.catalog.combiner
        servers: dict[str, list[tuple[str, str]]] = {}
        for name, desc in sorted(tools.items()):
            server = name.split("_")[0]
            if server not in servers:
                servers[server] = []
            servers[server].append((name, desc))

        sel = self.query_one("#mcp_server", Select)
        options = [(s, s) for s in sorted(servers)]
        if options:
            sel.set_options(options)
            sel.value = options[0][0]
            self._servers = servers
            self._build_mcp_table()

        table = self.query_one("#mcp_table", DataTable)
        table.cursor_type = "row"

    def _build_mcp_table(self) -> None:
        table = self.query_one("#mcp_table", DataTable)
        table.clear(columns=True)
        table.add_columns("Tool", "Enabled", "Description")
        server = self.query_one("#mcp_server", Select).value
        if not hasattr(self, '_servers') or server not in self._servers:
            return
        agent_tools = self.agent.tools if isinstance(self.agent.tools, dict) else {}
        for name, desc in self._servers[server]:
            enabled = "✓" if agent_tools.get(name, True) else "✗"
            table.add_row(name, enabled, desc[:80], key=name)
        info = self.query_one("#mcp_tool_info", Static)
        info.update(f"{len(self._servers[server])} tools │ [Space] toggle enabled/disabled")

    @on(Select.Changed)
    def _on_mcp_server_change(self, event: Select.Changed) -> None:
        if event.select.id == "mcp_server":
            self._build_mcp_table()

    @on(DataTable.RowSelected)
    def _on_mcp_toggle(self, event: DataTable.RowSelected) -> None:
        if event.control.id != "mcp_table":
            return
        key = event.row_key
        if key is None:
            return
        tool_name = str(key.row_key.value) if key.row_key.value else ""
        agent_tools = dict(self.agent.tools) if isinstance(self.agent.tools, dict) else {}
        current = agent_tools.get(tool_name, True)
        agent_tools[tool_name] = not current
        result = save_agent(self.agent, {"tools": agent_tools}, STATE.sources)
        if result.success:
            self.notify(f"{tool_name}: {'ENABLED' if not current else 'DISABLED'}", timeout=2)
            STATE.reload()
            self.agent = next((a for a in STATE.agents if a.name == self.agent.name), self.agent)
            self._build_mcp_table()
        else:
            self.notify(f"Save failed: {'; '.join(result.errors)}", severity="error")

    # ── Prompt pane ──────────────────────────────────────────────────────

    def _prompt_pane(self) -> Container:
        if self.agent.prompt:
            preview = "\n".join(self.agent.prompt.split("\n")[:30])
            if len(self.agent.prompt.split("\n")) > 30:
                preview += f"\n\n... (truncated, full prompt is {len(self.agent.prompt)} chars)"
        else:
            preview = "(no prompt body)"
        path_info = f"Source: {self.agent.source_path}" if self.agent.source_path else "Source: inline config"
        return Container(
            Horizontal(
                Static(path_info, id="prompt_source"),
                Button("Open in $EDITOR", id="prompt_edit", variant="primary"),
                id="prompt_header",
            ),
            Static(preview, id="prompt_body", markup=False),
            id="prompt_pane",
        )

    # ── Raw pane ─────────────────────────────────────────────────────────

    def _raw_pane(self) -> Container:
        import json as _json
        a = self.agent
        raw = {
            "name": a.name, "mode": a.mode, "model": a.model,
            "variant": a.variant, "temperature": a.temperature,
            "color": a.color, "hidden": a.hidden, "disabled": a.disabled,
            "steps": a.steps, "description": a.description,
            "permission_count": len(a.permission),
            "tools_count": len(a.tools),
            "source_type": a.source_type, "source_path": a.source_path,
            "shadowed": a.shadowed,
            "options": a.options,
        }
        text = _json.dumps(raw, indent=2, default=str)
        return Container(
            Static(f"Options/unknown keys: {a.options}", id="raw_info"),
            Static(text, id="raw_body", markup=False),
            id="raw_pane",
        )

    # ── Save ─────────────────────────────────────────────────────────────

    def action_save(self) -> None:
        updates: dict[str, Any] = {}
        for field, wid_id in (("model", "cfg_model"), ("variant", "cfg_variant"), ("color", "cfg_color")):
            w = self.query_one(f"#{wid_id}", Input)
            if w.value:
                updates[field] = w.value

        t_widget = self.query_one("#cfg_temp", Input)
        if t_widget.value:
            try:
                updates["temperature"] = float(t_widget.value)
            except ValueError:
                self.notify("Invalid temperature", severity="error")
                return

        mode_widget = self.query_one("#cfg_mode", Select)
        if mode_widget.value != Select.BLANK:
            updates["mode"] = mode_widget.value

        steps_widget = self.query_one("#cfg_steps", Input)
        if steps_widget.value:
            try:
                updates["steps"] = int(steps_widget.value)
            except ValueError:
                self.notify("Invalid steps value", severity="error")
                return

        desc_widget = self.query_one("#cfg_desc", TextArea)
        updates["description"] = desc_widget.text
        updates["disable"] = not self.query_one("#cfg_enabled", Switch).value
        updates["hidden"] = self.query_one("#cfg_hidden", Switch).value

        result = save_agent(self.agent, updates, STATE.sources)
        if result.success:
            self.notify(f"Saved: {result.changes}\n⚠ Restart opencode for changes to take effect.", timeout=6)
            STATE.reload()
            self.agent = next((a for a in STATE.agents if a.name == self.agent.name), self.agent)
        else:
            self.notify(f"Save failed: {'; '.join(result.errors)}", severity="error", timeout=8)

    @on(Button.Pressed)
    def _on_detail_button(self, event: Button.Pressed) -> None:
        if event.button.id == "prompt_edit":
            if self.agent.source_path:
                import subprocess, os
                editor = os.environ.get("EDITOR", "vim")
                subprocess.run([editor, self.agent.source_path])
                STATE.reload()
                self.agent = next((a for a in STATE.agents if a.name == self.agent.name), self.agent)
                self.notify("Reloaded after editor", timeout=2)
            else:
                self.notify("No source file — agent is inline JSON", severity="warning")

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_prev_tab(self) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        names = ["tab_config", "tab_perms", "tab_mcp", "tab_prompt", "tab_raw"]
        c = tabs.active
        i = names.index(c) if c in names else 0
        tabs.active = names[(i - 1) % len(names)]

    def action_next_tab(self) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        names = ["tab_config", "tab_perms", "tab_mcp", "tab_prompt", "tab_raw"]
        c = tabs.active
        i = names.index(c) if c in names else 0
        tabs.active = names[(i + 1) % len(names)]

    def action_export_md(self) -> None:
        if self.agent.source_type == "markdown":
            self.notify("Already a markdown agent", severity="information")
            return
        import yaml
        fm = {"mode": self.agent.mode}
        if self.agent.model:
            fm["model"] = self.agent.model
        if self.agent.temperature is not None:
            fm["temperature"] = self.agent.temperature
        if self.agent.description:
            fm["description"] = self.agent.description
        if self.agent.color:
            fm["color"] = self.agent.color
        if self.agent.permission:
            fm["permission"] = dict(self.agent.permission)
        yaml_body = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
        content = f"---\n{yaml_body}---\n\n"
        target_dir = GLOBAL_CONFIG_DIR / "agents"
        target_dir.mkdir(parents=True, exist_ok=True)
        filepath = target_dir / f"{self.agent.name}.md"
        _atomic_write(filepath, content)
        self.notify(f"Exported to {filepath}\nRemove inline definition and restart.", timeout=6)
        STATE.reload()
        self.agent = next((a for a in STATE.agents if a.name == self.agent.name), self.agent)


# ═══════════════════════════════════════════════════════════════════════════
# Add Pattern Screen
# ═══════════════════════════════════════════════════════════════════════════

class AddPatternScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "add", "Add")]

    def __init__(self, agent: AgentDef) -> None:
        super().__init__()
        self.agent = agent

    def compose(self) -> ComposeResult:
        yield Container(
            Static("Add Custom Permission Pattern", id="add_title"),
            Horizontal(Label("Pattern:"), Input(placeholder='e.g. mcp-combiner__github_*', id="add_pattern")),
            Horizontal(Label("Pattern:"), Select([("allow", "allow"), ("ask", "ask"), ("deny", "deny")], value="allow", id="add_action")),
            Horizontal(
                Button("Add", id="add_btn", variant="primary"),
                Button("Cancel", id="cancel_btn"),
                id="add_btns",
            ),
            id="add_box",
        )

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_add(self) -> None:
        pattern = self.query_one("#add_pattern", Input).value.strip()
        if not pattern:
            self.notify("Pattern is required", severity="error")
            return
        action = self.query_one("#add_action", Select).value
        perms = dict(self.agent.permission)
        perms[pattern] = action
        result = save_agent(self.agent, {"permission": perms}, STATE.sources)
        if result.success:
            self.notify(f"Added: {pattern} = {action}", timeout=3)
            STATE.reload()
        else:
            self.notify(f"Failed: {'; '.join(result.errors)}", severity="error")
        self.app.pop_screen()

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        if event.button.id == "add_btn":
            self.action_add()
        elif event.button.id == "cancel_btn":
            self.action_cancel()


# ═══════════════════════════════════════════════════════════════════════════
# Message Screen
# ═══════════════════════════════════════════════════════════════════════════

class MessageScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("q", "dismiss", "Close")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._msg = message

    def compose(self) -> ComposeResult:
        yield Container(
            Static(self._msg, id="msg_body", markup=False),
            Button("Close", id="msg_close"),
            id="msg_box",
        )

    def action_dismiss(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        if event.button.id == "msg_close":
            self.app.pop_screen()


# ═══════════════════════════════════════════════════════════════════════════
# Backup Screen
# ═══════════════════════════════════════════════════════════════════════════

class BackupScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("r", "restore", "Restore"),
        Binding("d", "delete_backup", "Delete"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Backups", id="backup_title")
        yield DataTable(id="backup_table")
        yield Footer()

    def on_mount(self) -> None:
        from .backup import list_backups
        table = self.query_one("#backup_table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Timestamp", "Source", "Size")
        self._backups = list_backups()
        for b in self._backups:
            table.add_row(b["timestamp"], b["source_file"],
                          f"{b['size_bytes'] / 1024:.1f} KB", key=b["timestamp"])

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_restore(self) -> None:
        table = self.query_one("#backup_table", DataTable)
        if table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(table.cursor_coordinate)
        if key is None:
            return
        ts = str(key.row_key.value)
        backup = next((b for b in self._backups if b["timestamp"] == ts), None)
        if backup is None:
            return
        from .backup import restore_backup
        restored = restore_backup(backup["path"])
        if restored:
            self.notify(f"Restored: {restored}\n⚠ Restart opencode.", timeout=6)
            STATE.reload()
        else:
            self.notify("Restore failed", severity="error")

    def action_delete_backup(self) -> None:
        table = self.query_one("#backup_table", DataTable)
        if table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(table.cursor_coordinate)
        if key is None:
            return
        ts = str(key.row_key.value)
        backup = next((b for b in self._backups if b["timestamp"] == ts), None)
        if backup is None:
            return
        backup["path"].unlink(missing_ok=True)
        self.notify(f"Deleted backup {ts}", timeout=3)
        self.on_mount()


# ═══════════════════════════════════════════════════════════════════════════
# Help Screen
# ═══════════════════════════════════════════════════════════════════════════

class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("q", "dismiss", "Close")]

    HELP = """\
 Main Screen
  Enter     Edit selected agent
  /         Search/filter agents
  r         Reload config from disk
  h         Toggle show shadowed agents
  m         Cycle mode filter (all → primary → subagent)
  n         Create new agent
  c         Clone selected agent
  Delete    Delete selected agent
  Backspace Open backup manager
  q         Quit
  ?         This help screen

 Detail Screen
  Escape    Back to agent list
  Ctrl+S    Save changes
  ← →      Switch tabs
  e         Export inline agent → markdown file

 Permissions Tab
  Enter     Toggle allow → ask → deny for selected tool
  *         Set wildcard pattern for entire group
  p         Preview ruleset trace (findLast order)
  +         Add custom permission pattern
  Tab       Switch tool group (built-in / combiner / local / browser)
"""

    def compose(self) -> ComposeResult:
        yield Container(
            Static(self.HELP, id="help_body", markup=False),
            Button("Close", id="help_close"),
            id="help_box",
        )

    def action_dismiss(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        if event.button.id == "help_close":
            self.app.pop_screen()


# ═══════════════════════════════════════════════════════════════════════════
# App
# ═══════════════════════════════════════════════════════════════════════════

class OpencodeAgentTUI(App):
    TITLE = "opencode-agent-tui"
    SUB_TITLE = "Agent config editor"
    CSS = """
    #main { height: 100%; }
    #toolbar { height: 3; margin: 1; }
    #toolbar Input { width: 40; }
    #info { height: 1; margin: 0 2; color: $text-muted; }
    #body { height: 1fr; }
    #agents { width: 2fr; }
    #preview { width: 1fr; border: solid $primary; padding: 1; }
    #preview_text { height: 1fr; margin-bottom: 1; }
    #preview_actions { height: 3; align: right bottom; }

    #detail { height: 100%; }
    #detail_title { height: 1; margin: 1; text-style: bold; }
    #tabs { height: 1fr; }

    #cfg_row { height: auto; margin: 1; }
    .labels { width: 14; }
    .fields { width: 1fr; }
    #switches { width: 20; }
    #cfg_desc { height: 8; margin: 1; }

    #perms_pane { height: 100%; }
    #perm_toolbar { height: 3; margin: 1; }
    #perm_toolbar Select { width: 20; }
    #perm_info { width: 1fr; margin: 0 2; }
    #perm_table { height: 1fr; }

    #mcp_pane { height: 100%; }
    #mcp_server { width: 30; margin: 1; }
    #mcp_tool_info { height: 1; margin: 0 2; color: $text-muted; }
    #mcp_table { height: 1fr; }

    #prompt_header { height: 3; margin: 1; }
    #prompt_source { width: 1fr; color: $text-muted; }
    #prompt_body { height: 1fr; margin: 1; }

    #raw_info { height: 1; margin: 1; color: $text-muted; }
    #raw_body { height: 1fr; margin: 1; }

    #new_title { height: 1; margin: 1; text-style: bold; }
    #new_form { margin: 1; height: auto; }
    #new_form Horizontal { height: 3; }
    #new_form Input { width: 40; }
    #new_actions { margin-top: 1; }

    #confirm_box { margin: 5 10; padding: 1; border: solid $error; }
    #confirm_msg { text-align: center; }
    #confirm_btns { align: center middle; height: 3; }

    #add_box { margin: 5 10; padding: 1; border: solid $primary; }
    #add_title { text-style: bold; margin-bottom: 1; }
    #add_box Input { width: 40; }
    #add_box Select { width: 15; }
    #add_btns { margin-top: 1; align: right middle; }

    #msg_box { margin: 5 10; padding: 1; border: solid $primary; height: auto; }
    #msg_body { margin-bottom: 1; }

    #help_box { margin: 2 5; padding: 1; border: solid $primary; height: auto; }
    #help_body { height: auto; }

    #backup_title { height: 1; margin: 1; text-style: bold; }
    #backup_table { height: 1fr; }

    DataTable { height: 1fr; }
    """

    def on_mount(self) -> None:
        self.push_screen(MainScreen())


def main() -> None:
    OpencodeAgentTUI().run()
