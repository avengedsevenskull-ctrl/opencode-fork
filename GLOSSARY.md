# GLOSSARY

Domain language for this fork. One concept, one name. Harness-wide terms
(`session`, `execution`, `landing`) are defined in the private global
`~/.config/opencode/GLOSSARY.md`; this file adds the fork and TUI tab terms.

## session

One OpenCode conversation. The user calls a session a "tab"; the TUI renders
it as a **session tab**. The canonical harness definition lives in the global
glossary.

## session tab

The TUI's visual representation of one session: title, repo or directory
detail, status, unread state, and close affordance. Several session tabs can
point at sessions in different repositories.

## repo group

A group of session tabs whose sessions share a git repository. Worktrees of
the same repository belong to the same repo group. `opencode-project-tabs`
implements this by sorting the built-in tab bar by project and listing the
groups in the session sidebar.

## directory group

A group for sessions outside a git repository, keyed by the session's
directory.

## named session set

A saved, named collection of sessions that can be reopened as tabs. Provided
by `opencode-session-tabs`. The plugin UI may call these "workspaces"; this
fork does not. A named session set is not an OpenCode workspace.

## workspace

OpenCode's provider-backed workspace object (`packages/core/src/workspace.ts`):
a provider plus a binding to a real environment. A workspace is not a tab set
and is never a synonym for a named session set.

## last-state restore

The built-in TUI behavior that reopens the tabs from the previous run. It is
separate from a named session set and stays enabled.

## tab scope

`tabs.scope`. `cwd` keeps a separate tab list per launch directory; `global`
shares one tab list across launch directories on the same TUI host. It does
not sync tabs across devices.

## daily driver

The installed `/usr/bin/opencode` built from the `main` branch.

## upgrade branch

`upgrade-2.1.2`, the branch that ports the fork to the `v2.1.2` tag.

## trial

Running the TUI via `bun run dev` from a worktree, before installing it as the
daily driver.

## clear

The `/clear` command. Starts a new session like `/new`, but forces the new
session's location to the current session's location (directory and workspace)
instead of following `session.new_location`. Falls back to the launch
directory when there is no current session or its location is unavailable.
