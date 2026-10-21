import { Plugin } from "@opencode/plugin/tui"

// Test-only probe: the plugin router exposes the home route without its location,
// so render the runtime field the TUI uses to carry a new session's directory.
export default Plugin.define({
  id: "fixture.route-probe",
  setup(context) {
    context.ui.slot({
      append: "home.footer",
      render: () => {
        const route = context.ui.router.current() as {
          readonly type: string
          readonly location?: { readonly directory?: string }
        }
        return <text>{`route:${route.location?.directory ?? "none"}`}</text>
      },
    })
  },
})
