import { expect, test } from "bun:test"
import { createAppFixture } from "./fixture/app"
import { copyPlugin, tmpdir } from "./fixture/fixture"
import { directory, json } from "./fixture/tui-client"

const launch = "/tmp/opencode/launch"
const location = { directory, project: { id: "project", directory, canonical: directory } }
const launchLocation = { directory: launch, project: { id: "project", directory: launch, canonical: launch } }
const session = {
  id: "ses_clear",
  title: "Session to clear",
  projectID: "project",
  location: { directory },
  agent: "build",
  model: { providerID: "provider", id: "model" },
  cost: 0,
  tokens: { input: 0, output: 0, reasoning: 0, cache: { read: 0, write: 0 } },
  time: { created: 0, updated: 0 },
}

function render(state: string, plugin: string) {
  const cleared = Promise.withResolvers<string>()
  return createAppFixture({
    state,
    args: { sessionID: session.id },
    config: { animations: false, tabs: { mode: "on" }, plugins: [plugin] },
    fetch: (url, request) => {
      if (url.pathname === `/api/session/${session.id}/clear`) {
        cleared.resolve(request.method)
        return new Response(null, { status: 204 })
      }
      if (url.pathname === "/api/fs/list") return json({ location: launchLocation, data: [] })
      if (url.pathname === "/api/location")
        return json(url.searchParams.has("location[directory]") ? location : launchLocation)
      if (url.pathname === "/api/session") return json({ data: [session], cursor: {} })
      if (url.pathname === `/api/session/${session.id}`) return json({ data: session })
      if (/^\/api\/session\/[^/]+\/(message|inbox|permission)$/.test(url.pathname))
        return json({ data: [], cursor: {} })
      if (url.pathname === "/api/agent")
        return json({ location, data: [{ id: "build", mode: "primary", hidden: false, permissions: [] }] })
      if (url.pathname === "/api/provider") return json({ location, data: [{ id: "provider", name: "Provider" }] })
      if (url.pathname === "/api/model")
        return json({
          location,
          data: [{ id: "model", providerID: "provider", name: "Model", variants: [] }],
        })
    },
  }).then((setup) => Object.assign(setup, { cleared: cleared.promise }))
}

test("/clear replaces the active session tab and inherits its location", async () => {
  await using state = await tmpdir()
  await using plugin = await copyPlugin("route-probe")
  await using setup = await render(state.path, plugin.directory)

  await setup.waitForFrame((frame) => frame.includes("Session to clear"))
  await setup.mockInput.typeText("/clear")
  setup.mockInput.pressEnter()
  const frame = await setup.waitForFrame(
    (frame) => !frame.includes("Session to clear") && frame.includes(`route:${directory}`),
  )

  expect(await setup.cleared).toBe("POST")
  expect(frame).not.toContain(`route:${launch}`)
}, 15_000)

test("/new keeps the active session tab and follows session.new_location", async () => {
  await using state = await tmpdir()
  await using plugin = await copyPlugin("route-probe")
  await using setup = await render(state.path, plugin.directory)

  await setup.waitForFrame((frame) => frame.includes("Session to clear"))
  await setup.mockInput.typeText("/new")
  setup.mockInput.pressEnter()
  const frame = await setup.waitForFrame(
    (frame) => frame.includes("New session") && frame.includes("Session to clear") && frame.includes("route:"),
  )

  expect(frame).toContain("Session to clear")
  expect(frame).toContain(`route:${launch}`)
}, 15_000)
