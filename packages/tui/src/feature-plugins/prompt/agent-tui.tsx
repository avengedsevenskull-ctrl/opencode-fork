import { Plugin } from "@opencode/plugin/tui"
import { createSignal } from "solid-js"
import { useTheme } from "../../context/theme"

// Fork add-on: a gear icon in the prompt footer that launches the external
// opencode-agent-tui config editor in a terminal, plus the matching command.
// Kept as a self-contained feature plugin so upstream app.tsx / prompt files
// stay untouched.

function launchAgentTui() {
  const cwd = `${process.env.HOME}/.config/opencode`
  // Keep the terminal open on close so the user can read any errors.
  const wrapped = `which opencode-agent-tui >/dev/null 2>&1 && opencode-agent-tui 2>&1; echo; echo "Exit code: $?"; read -p 'Press enter to close...'`

  if (process.env.KITTY_WINDOW_ID) {
    Bun.spawn(["kitty", "@", "launch", "--cwd", cwd, "--", "bash", "-c", wrapped])
    return
  }
  if (process.env.GHOSTTY || (process.env.TERM ?? "").includes("ghostty")) {
    Bun.spawn(["ghostty", "-e", "bash", "-c", `cd ${cwd} && ${wrapped}`])
    return
  }
  if (process.env.TMUX) {
    Bun.spawn(["tmux", "split-window", "-c", cwd, "bash", "-c", wrapped])
    return
  }

  const terminals = [
    "kitty",
    "ghostty",
    "alacritty",
    "gnome-terminal",
    "konsole",
    "xfce4-terminal",
    "x-terminal-emulator",
    "xterm",
  ]
  for (const term of terminals) {
    if (Bun.spawnSync(["which", term]).exitCode !== 0) continue
    if (term === "alacritty") Bun.spawn(["alacritty", "-e", "bash", "-c", `cd ${cwd} && ${wrapped}`])
    else if (term === "kitty") Bun.spawn(["kitty", "--", "bash", "-c", `cd ${cwd} && ${wrapped}`])
    else Bun.spawn([term, "-e", "bash", "-c", `cd ${cwd} && ${wrapped}`])
    return
  }
  Bun.spawn(["bash", "-c", wrapped], { cwd, stdio: ["inherit", "inherit", "inherit"] })
}

export default Plugin.define({
  id: "opencode.agent-tui",
  setup(context) {
    context.ui.slot({
      append: "prompt.footer.status",
      render: () => {
        const theme = useTheme()
        const [hovered, setHovered] = createSignal(false)
        const shortcut = () => context.keymap.shortcuts("agent-tui.open")[0]
        return (
          <box
            flexShrink={0}
            onMouseOver={() => setHovered(true)}
            onMouseOut={() => setHovered(false)}
            onMouseUp={() => context.keymap.dispatch("agent-tui.open")}
          >
            <text fg={hovered() ? theme.text.base : theme.text.muted} wrapMode="none">
              <span style={{ fg: hovered() ? theme.text.base : theme.text.muted }}>⚙</span>
              {shortcut() ? ` ${shortcut()}` : ""}
            </text>
          </box>
        )
      },
    })

    context.ui.slot({
      append: "app",
      render() {
        context.keymap.layer(() => ({
          mode: "global",
          commands: [
            {
              id: "agent-tui.open",
              title: "Agent config",
              description: "Open the opencode-agent-tui agent configuration editor in a terminal",
              group: "Tools",
              palette: true,
              slash: { name: "agent-tui" },
              run: () => {
                launchAgentTui()
              },
            },
          ],
        }))
        return null
      },
    })
  },
})
