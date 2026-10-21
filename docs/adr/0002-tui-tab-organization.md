# ADR 0002 — TUI tab organization: project sorting plus named session sets

- Status: Accepted
- Date: 2026-10-21
- Deciders: fork maintainer

## Context

The TUI already renders one tab per session, with horizontal and vertical
layouts, drag reorder, unread jumps, history, close/reopen, project/branch
detail on the second line, and last-state restore. It does not group tabs by
repository, search all open tabs, or save named collections.

Three community plugins were evaluated:

- `opencode-better-tabs` (igorovh) — true repo-grouped vertical bar, activity
  counters, collapse, two-key jumps, `/tabs` search. Built against OpenCode
  2.0.26 / OpenTUI 0.5.17; overlays the built-in bar; registers `/tabs`.
- `opencode-project-tabs` (cnicolov) — sorts the built-in bar by project and
  recency and adds a "Tabs by project" sidebar with last-active times and
  status dots. Requires 2.0.28+; no slash command; lower risk.
- `opencode-session-tabs` (briansunter) — global session picker, batch open,
  inline preview, named saved sets, agent tool. Tested on 2.0.30; registers
  `/tabs` and `/workspaces`.

`better-tabs` and `session-tabs` both register `/tabs`, so they cannot both
own that slash command without patching one. Keybindings can be remapped; the
slash name cannot.

## Decision

The daily driver uses:

- **`opencode-project-tabs`** for project organization: the built-in tab bar is
  sorted by repo and recency, and the sidebar lists projects with last-active
  times and status dots.
- **`opencode-session-tabs`** for named session sets and the global picker.

TUI settings:

```json
{
  "tabs": {
    "mode": "on",
    "scope": "global",
    "layout": "vertical",
    "indicators": "numbers"
  }
}
```

`opencode-better-tabs` is deferred. It may be adopted later only if its
`/tabs` slash command is patched to a different name and the overlay is
verified on the installed OpenTUI version.

## Alternatives considered

- **`better-tabs` + `session-tabs`** — better visual grouping, but the `/tabs`
  slash collision is ambiguous and the overlay is unverified on OpenTUI 0.6.6.
- **`better-tabs` only** — no named session sets.
- **`session-tabs` only** — no project sorting/grouping.
- **Built-ins only** — no grouping, search, or named sets.

## Consequences

- Project organization is sorting plus a sidebar list, not collapsible group
  headers in the tab bar. This is the deliberate cost of avoiding the
  `better-tabs` overlay and slash collision.
- `project-tabs` re-sorts by project and recency, so `Ctrl+number` targets move
  as sessions become active. Accept dynamic positions, use the sidebar list, or
  turn sorting off; do not claim stable number targets.
- Both plugins are young, single-author packages and must be trialed with real
  sessions before they are trusted. Either can be disabled with one config
  edit.
- Plugin storage is local to the TUI host. Cross-device consistency comes from
  running one TUI in tmux (ADR 0003), not from the plugins.
- `tabs.scope: "global"` keeps one tab list across launch directories on that
  host; it is not a cross-device sync mechanism.

## Guards

- `opencode plugin list` shows both TUI plugins.
- Vertical tabs show project-sorted session tabs and the "Tabs by project"
  sidebar.
- `<leader>p` opens the session picker; `<leader>k` opens named session sets.
- Last-state restore still works after restarts.
- If either plugin fails on OpenTUI 0.6.6, disable it and keep the other; do
  not install `better-tabs` as a workaround without the slash-command patch.
