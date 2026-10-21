# ADR 0003 — Cross-device TUI continuity uses tmux on the host

- Status: Accepted
- Date: 2026-10-21
- Deciders: fork maintainer

## Context

The user SSHes into the Linux host and runs the OpenCode TUI there. The phone
and laptop are SSH clients, not separate OpenCode servers. The shared
`opencode serve --service` process shares sessions, but not TUI tab state:
session tabs and plugin storage are client-local.

Opening several independent TUIs would give each client its own tab list and
could race on tab-storage writes. It would not give the phone and laptop the
same tab state.

## Decision

Run **one TUI inside a tmux session on the host**. Attach from Alacritty,
Termux, or any SSH client. The TUI process, tabs, named session sets, and
last-state restore are then the same on every device.

Use `tabs.scope: "global"` inside that single TUI so restarting the TUI from a
different directory keeps the same tab list. The host must stay awake and
reachable for phone access.

## Alternatives considered

- **Multiple independent TUIs** — separate tab state per client; no
  cross-device continuity; possible tab-storage races.
- **`tabs.scope: "global"` without tmux** — shares a tab list only among TUIs
  using the same host-local storage; it does not sync devices and can race.
- **A tab-state sync plugin** — new server-side work, outside this upgrade.

## Consequences

- tmux 3.7c is already installed on the host.
- The phone and laptop see exactly the same TUI; attaching a second client
  mirrors the screen rather than creating a second workspace.
- Named session sets are local to that one TUI process, which is correct
  because every device attaches to it.
- If the host sleeps or loses network, phone access stops. That is an
  availability issue, not a tab-state issue.
- Do not run a second TUI in another tmux window and expect independent tab
  state while `scope` is `global`.

## Guards

- `tmux attach -t opencode` from the laptop and phone shows the same tabs.
- Detaching and reattaching preserves the active tab and transcript position.
- `tabs.scope` is `global` in `~/.config/opencode/cli.json`.
- A named session set created on the laptop is visible after reattaching from
  the phone.
