# Plan — TUI tabs + v2.1.2 fork upgrade (one-shot)

- Date: 2026-10-21
- Status: Ready to execute
- Audience: a fresh driver agent with this repository checked out
- Private companion: `~/.config/opencode/docs/handoffs/2026-10-21-tui-tabs-upgrade.md`
  (contains the private repository URL, host paths, and backup locations)

## Objective

Take the installed TUI from `v2.0.12-6-g6c016fd40d` to a v2.1.2-based fork,
keep all six local features, add `/clear` directory preservation, and install
the chosen TUI tab organization stack:

- `opencode-project-tabs` for project sorting and the "Tabs by project"
  sidebar;
- `opencode-session-tabs` for named session sets and the global picker;
- vertical tabs, `tabs.mode: "on"`, `tabs.scope: "global"`;
- one TUI inside tmux on the host, attachable from laptop and phone.

The daily driver must remain on the old build until the new build passes its
trial. Execute every phase in one run; the 3-day trial is an observation
window after install, not a checkpoint for approval.

## Non-negotiable rules

1. **Run this runbook from a plain shell, not from inside the TUI being
   upgraded.** `opencode service stop`/`restart` would terminate the agent's
   own session. If the agent must run inside OpenCode, the final service
   restart is a human step.
2. **Never push the harness archive to the public fork.** The fork is public
   and GitHub cannot make a public fork private by itself. The archive belongs
   in the private harness repository.
3. **Never `git add -A` before the archive is moved out.** Move first, then
   stage deletions explicitly.
4. **Never `sudo cp` a built binary.** Install only through the PKGBUILD.
5. **Never let `makepkg` strip the binary.** The PKGBUILD must keep
   `options=('!strip' '!debug')`.
6. **Never trust a plugin before it renders with real sessions.** Both chosen
   plugins are unverified on OpenTUI 0.6.6.
7. **Stop on a red gate.** Do not continue past a failed typecheck, lint, test,
   version check, DB check, or plugin render. Report the failure.
8. **One writer at the shared state.** This run is a single driver; no
   parallel builders or concurrent merges.

## Current state (verified 2026-10-21)

- Repo: the `custom-opencode` checkout
- Branch: `upgrade-v2`
- HEAD: `6c016fd40d` (`v2.0.12-6-g6c016fd40d`)
- Installed binary: `/usr/bin/opencode`, `v2.0.12.r18154.g6c016fd40d`
- Latest stable tag: `v2.1.2` at `a517687fde98a7329ada3ae644a01a9fc6c4e99e`
- Target branch: `upgrade-2.1.2` (new), merged to `main` after trial
- Working tree: dirty with the 2026-10-20 harness archive migration
  (tracked deletions plus untracked `.archived-2026-10-20` paths)
- Database: `~/.local/share/opencode/opencode.db`, about **6.8 GB**;
  `sqlite3` and `tmux 3.7c` are installed; `/home` has about 46 GB free
- Public fork default branch: `dev` (will become `main`)
- Private harness repo: `~/.config/opencode`, default `main`, clean, contains
  `GLOSSARY.md`, `archive/`, and `docs/`

The six feature commits, oldest first:

```text
2a4b9aa476 chore: vendor opencode-agent-tui config editor
519cbc0fd7 feat(tui): agent-tui gear plugin in prompt footer
bf63edeab2 feat(tui): show session id in subagent messages
d07fdb0c2b docs: rebase fork on v2.0.12 and ignore .codegraph
8db30dde4e chore(opencode): default local agents to deepseek/deepseek-flash
6c016fd40d feat(commands): complete declared command arguments
```

## Decisions

| Decision | Choice |
| --- | --- |
| Target ref | `v2.1.2` tag, exactly |
| Baseline | current state becomes `main`; public fork receives deletion + docs only |
| Harness archive | private harness repo, `archive/2026-10-20-harness/` |
| Local commits | preserve all six |
| Project organization | `opencode-project-tabs` |
| Named sets | `opencode-session-tabs` |
| Visual grouping | `better-tabs` deferred; requires a `/tabs` slash patch |
| TUI layout | vertical, numbers, `mode: "on"`, `scope: "global"` |
| Cross-device | one TUI inside tmux on the host |
| `/clear` | new session at the current session's location; `/new` keeps its config |
| Backup | SQLite backup + config + old binary before first launch |
| Rollback | restore backup / rebuild old tag; 3-day trial |

## Phase 0 — Preflight

Run from a plain **bash** shell in the repo (the user's interactive shell is
fish; these commands use bash syntax).

```bash
cd "$HOME/Projects/coding/custom-opencode"
git status --short
git log --oneline -8
git rev-parse v2.1.2
command -v bun sqlite3 tmux gh
opencode --version
```

Gate:

- HEAD is `6c016fd40d`.
- `v2.1.2` resolves to `a517687fde...`.
- The working tree matches the 2026-10-20 archive migration plus the new
  `GLOSSARY.md` and `docs/` from this plan; no unrelated edits.
- `sqlite3`, `tmux`, `bun`, and `gh` are available.

Tag the six feature commits before adding anything:

```bash
git tag fork-features-2026-10-21 6c016fd40d
```

This tag is the cherry-pick source for the upgrade branch. Do not tag after
the archive commit.

## Phase 1 — Move the harness archive to the private repo

```bash
mkdir -p "$HOME/.config/opencode/archive/2026-10-20-harness"
git ls-files --others --exclude-standard | grep 'archived-2026-10-20' | while IFS= read -r path; do
  [ -n "$path" ] || continue
  dest="$HOME/.config/opencode/archive/2026-10-20-harness/${path#./}"
  mkdir -p "$(dirname "$dest")"
  mv "$path" "$dest"
done
```

Verify the public tree now has deletions only:

```bash
git status --short
```

It must show the tracked ` D ...` deletions plus the new `?? GLOSSARY.md` and
`?? docs/`; no `?? ...archived-2026-10-20...` paths remain.

Commit the archive in the private repo:

```bash
cd "$HOME/.config/opencode"
git status --short
git add archive/2026-10-20-harness
git commit -m "chore(archive): preserve 2026-10-20 project harness"
git push origin main
```

Gate:

- `git -C "$HOME/.config/opencode" log -1 --oneline` shows the archive commit.
- `git -C "$HOME/.config/opencode" status --short` is clean.
- The private repo is confirmed private (`gh repo view ... --json isPrivate`).
- `cli.json` and `.secrets/` were not staged.

## Phase 2 — Create the public baseline `main`

Back in the public fork:

```bash
cd "$HOME/Projects/coding/custom-opencode"
git add -u
git commit -m "chore(harness): remove project harness for global-only load path"
git add GLOSSARY.md docs/adr docs/plans
git commit -m "docs(fork): record v2.1.2 TUI tab upgrade decisions"
git switch -c main
git push -u origin main --no-verify
gh repo edit --default-branch main
```

Update the PKGBUILD source:

```bash
sed -i 's/#branch=upgrade-v2/#branch=main/' "$HOME/.cache/yay/opencode-git/PKGBUILD"
grep '^source=' "$HOME/.cache/yay/opencode-git/PKGBUILD"
```

Gate:

- `main` contains the six features, the deletion commit, and the docs commit.
- No `.archived-2026-10-20` path is tracked.
- The PKGBUILD source is `#branch=main`.
- The public fork default branch is `main`.
- Do not build yet; `main` is still 2.0.12.

## Phase 3 — Port to v2.1.2

```bash
cd "$HOME/Projects/coding/custom-opencode"
git switch -c upgrade-2.1.2 main
git cherry-pick v2.0.12..fork-features-2026-10-21
```

Resolve conflicts in this order:

1. `packages/tui/src/feature-plugins/prompt/agent-tui.tsx` and
   `packages/tui/src/plugin/builtins.ts` — the TUI plugin API is the most
   likely change.
2. `packages/tui/src/routes/session/index.tsx` — subagent session id.
3. `packages/tui/src/component/prompt/autocomplete.tsx` — declared command
   arguments.
4. `packages/core/src/command.ts`, `packages/schema/src/config/command.ts`,
   and the plugin command types — command argument completion.
5. `README.md` and `.gitignore` — docs and `.codegraph`.

Gate after cherry-pick:

```bash
bun install --frozen-lockfile
bun run --cwd packages/tui typecheck
bun run lint
```

Do not proceed if any of these fail.

## Phase 4 — Make `/clear` preserve the current session's directory

History:

- On v2.0.12, `/clear` is an alias of `session.new`, so it follows
  `session.new_location` (default `launch`).
- On v2.1.2, `session.clear` is already a separate command (introduced in
  v2.0.13 by `4b381ac6a1`), but it still passes
  `config.data.session.new_location` to `newSessionLocation`, so it also
  usually goes to the launch directory. It also closes the current session tab
  via `sessionTabs.close()` — that is the “clear” behavior to keep.

Required behavior:

- `/clear` keeps its current behavior (close the current tab, start a new
  session, preserve model/agent) but forces the new session's location to the
  **current session's location** (directory and workspace), independent of
  `session.new_location`.
- `/new` keeps the configured behavior.
- If there is no current session or the current location is unavailable, fall
  back to the launch directory.

Implementation on `upgrade-2.1.2`:

1. In `packages/tui/src/app.tsx`, locate the `session.clear` command. It
   currently calls:

   ```ts
   newSessionLocation(
     config.data.session.new_location,
     data.location.default().directory,
     current,
     location.error?.location,
   )
   ```

   Change the first argument to `"inherit"`:

   ```ts
   newSessionLocation(
     "inherit",
     data.location.default().directory,
     current,
     location.error?.location,
   )
   ```

2. Keep `sessionTabs.close()`, the model/agent preservation, and
   `dialog.clear()` unchanged.
3. Add a short comment: `/clear` deliberately ignores `session.new_location`
   so it stays in the session's directory.
4. Extend `packages/tui/test/clear-session.test.tsx` to assert the cleared
   session's location equals the original session location, not the launch
   directory. Keep the existing `/new` tab-retention test.
5. Add/keep a focused test for the inherit/fallback path in
   `packages/tui/test/new-session-location.test.ts` if not already covered.

Gate:

```bash
bun run --cwd packages/tui typecheck
bun test packages/tui/test/clear-session.test.tsx
bun test packages/tui/test/new-session-location.test.ts
bun run lint
```

Manual gate after the trial build: from a session in `/repo/a`, `/clear`
opens a new session in `/repo/a`; `/new` still follows `session.new_location`.

## Phase 5 — Backup first, then trial the base TUI and install the tab stack

The first v2.1.2 process runs the three core DB migrations, so the backup must
happen **before** any v2.1.2 launch.

### 5.1 Backup

Run from a plain shell:

```bash
BACKUP="$HOME/.local/share/opencode/backups/2026-10-21-pre-2.1.2"
mkdir -p "$BACKUP"
sqlite3 "$HOME/.local/share/opencode/opencode.db" \
  ".backup '$HOME/.local/share/opencode/backups/2026-10-21-pre-2.1.2/opencode.db'"
cp "$HOME/.config/opencode/cli.json" \
  "$HOME/.local/share/opencode/backups/2026-10-21-pre-2.1.2/cli.json"
cp "$HOME/.config/opencode/opencode.json" \
  "$HOME/.local/share/opencode/backups/2026-10-21-pre-2.1.2/opencode.json"
cp /usr/bin/opencode \
  "$HOME/.local/share/opencode/backups/2026-10-21-pre-2.1.2/opencode-2.0.12.bin"
sqlite3 "$HOME/.local/share/opencode/backups/2026-10-21-pre-2.1.2/opencode.db" \
  "PRAGMA quick_check;"
```

`cli.json` contains the local service password; keep the backup private.

### 5.2 Trial

Create a trial copy of the DB so the live DB is not migrated yet, then run the
new TUI standalone (not through the existing background service). Run this in
the same shell as 5.1, or re-export `BACKUP`:

```bash
sqlite3 "$BACKUP/opencode.db" ".backup '$BACKUP/opencode-trial.db'"
git worktree add --detach /tmp/opencode-212-trial upgrade-2.1.2
cd /tmp/opencode-212-trial
bun install --frozen-lockfile
OPENCODE_DB="$BACKUP/opencode-trial.db" bun run dev --standalone
```

Base trial gate:

- TUI starts; `opencode --version` equivalent / app version is v2.1.2-based.
- The six local features work: agent-tui gear, subagent session id, declared
  command args, DeepSeek defaults, vendored editor, docs.
- `/clear` preserves the current session's directory.
- No crash in the composer or session timeline.

The backup was taken in 5.1; do not install plugins without it.

Install the plugins per their READMEs, and verify each with
`opencode plugin list`:

```bash
# project-tabs: package install first; fall back to the pinned path install
opencode plugin add github:cnicolov/opencode-project-tabs
# if that fails:
git clone --branch v0.1.0 https://github.com/cnicolov/opencode-project-tabs.git \
  "$HOME/.local/share/opencode-project-tabs"
# add the absolute path to the global plugins configuration in opencode.json
# (fallback: cli.json, if that is where your build loads it)

# session-tabs: package install
opencode plugin add github:briansunter/opencode-session-tabs
opencode plugin list
```

Commit the resulting plugin entries in the private harness repo (usually
`opencode.json`). Do not commit `cli.json`; it contains the local service
password.

Set the TUI config in `~/.config/opencode/cli.json`:

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

Do not install `better-tabs`. It registers `/tabs`, which collides with
`session-tabs`; adopt it later only after patching its slash command and
retesting on the installed OpenTUI.

Restart the standalone trial with the same `OPENCODE_DB` command so the
plugins load.

Plugin trial gate:

- Vertical tab strip renders.
- Tabs are sorted by project; the "Tabs by project" sidebar appears.
- Note: project sorting makes `Ctrl+number` targets move. Accept dynamic
  positions or turn sorting off; do not treat numbers as stable.
- `<leader>p` opens the session picker; `<leader>k` opens named session sets.
- Create a named set, restart the TUI, verify last-state restore and the
  named set both survive, then delete the test set.
- If either plugin fails on OpenTUI 0.6.6, disable it with one config edit and
  keep the other. Report the failure; do not replace it with `better-tabs`.

## Phase 6 — Build and install

After the trial and plugin gates pass:

```bash
cd "$HOME/Projects/coding/custom-opencode"
git worktree remove /tmp/opencode-212-trial
git switch main
git merge --ff-only upgrade-2.1.2
git push origin main --no-verify
```

Build through the PKGBUILD only:

```bash
cd "$HOME/.cache/yay/opencode-git"
rm -rf src pkg
makepkg -si --noconfirm
```

Verify:

```bash
which opencode                 # /usr/bin/opencode
opencode --version             # v2.1.2-based, never 1.4.2 / local / 0.0.0-dev
opencode service restart       # from a plain shell; this ends the current TUI
opencode service status
```

If `opencode --version` prints `1.4.2`, the binary was stripped; rebuild with
`options=('!strip' '!debug')`.

## Phase 7 — tmux cross-device layer

```bash
tmux new -s opencode
# inside: cd to a project and run opencode
# detach: Ctrl-b d
# reattach: tmux attach -t opencode
```

From the phone over SSH:

```bash
tmux attach -t opencode
```

Use one TUI in one tmux session. `tabs.scope: "global"` keeps the tab list
across launch directories on that host. Do not open a second independent TUI
and expect separate tab state while `global` is set.

## Phase 8 — Final verification

- [ ] `opencode --version` reports a v2.1.2-based fork.
- [ ] Installed plugin versions and sources are recorded in the final report.
- [ ] `opencode service status` is healthy.
- [ ] Six local features work.
- [ ] `/clear` starts a new session in the current session's directory;
      `/new` still follows `session.new_location`.
- [ ] Vertical tabs render repo groups and the "Tabs by project" sidebar.
- [ ] Named session sets create, reopen, and survive restart.
- [ ] Last-state restore works.
- [ ] tmux attach from laptop and phone shows the same tabs.
- [ ] DB backup exists and `PRAGMA quick_check` passed.
- [ ] Plugins can be disabled with one config edit.
- [ ] 3-day trial completed before deleting the old binary/backup.
- [ ] Old rebuild instructions are written down.

## Rollback

1. Stop the service from a plain shell: `opencode service stop`.
2. Restore the DB:
   `cp .../backups/2026-10-21-pre-2.1.2/opencode.db ~/.local/share/opencode/opencode.db`
   and remove stale `-wal`/`-shm` files.
3. Restore `cli.json` and `opencode.json` from the backup.
4. Reinstall the old binary from `opencode-2.0.12.bin` only if the PKGBUILD
   route is unavailable; prefer rebuilding from tag
   `fork-features-2026-10-21` by temporarily pointing the PKGBUILD at that
   tag and running `makepkg -si`.
5. Remove the plugin entries from the global config.
6. Restart the service and verify the old version.

## Commit / push map

| Repo | Commit | Push |
| --- | --- | --- |
| private harness | archive move | `origin main` |
| private harness | private handoff doc | `origin main` |
| public fork | harness deletions | `upgrade-v2` (then `main`) |
| public fork | GLOSSARY + ADRs + plan | `main` |
| public fork | six cherry-picks | `upgrade-2.1.2` |
| public fork | `/clear` change | `upgrade-2.1.2` |
| public fork | merge to `main` | `main` |

## Appendix A — Plugin matrix

| Plugin | Role | Requires | Install | Risk |
| --- | --- | --- | --- | --- |
| `opencode-project-tabs` | project sort + sidebar list | ≥2.0.28 | clone + absolute path | medium; no slash command |
| `opencode-session-tabs` | picker + named sets | tested 2.0.30 | `opencode plugin add github:...` | medium; TUI storage local |
| `opencode-better-tabs` | true repo groups + counters | ≥2.0.26 | deferred | high; overlay + `/tabs` collision |

## Appendix B — Exact feature commits

```text
2a4b9aa476
519cbc0fd7
bf63edeab2
d07fdb0c2b
8db30dde4e
6c016fd40d
```

Tag `fork-features-2026-10-21` points at the last one.

## Appendix C — Confirmed decisions and remaining risks

- **Confirmed 2026-10-21:** “directory from where `/clear` was run” means the
  **current session's location** (directory and workspace), not the OS
  directory the TUI was launched from. `/new` keeps `session.new_location`.
- **Remaining risk:** `project-tabs` and `session-tabs` load and render on
  OpenTUI 0.6.6. If not, disable the failing plugin; do not add `better-tabs`.
- **Constraint:** the final `opencode service restart` is performed from a
  plain shell, not from inside the TUI being upgraded.
