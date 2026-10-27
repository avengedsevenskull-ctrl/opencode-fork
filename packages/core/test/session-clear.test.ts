import { describe, expect } from "bun:test"
import { Deferred, Effect } from "effect"
import { AppNodeBuilder } from "@opencode/core/effect/app-node-builder"
import { LayerNode } from "@opencode/util/effect/layer-node"
import { Bus } from "@opencode/core/bus"
import { Database } from "@opencode/core/database/database"
import { Job } from "@opencode/core/job"
import { Location } from "@opencode/core/location"
import { LocationServiceMap } from "@opencode/core/location-service-map"
import { Project } from "@opencode/core/project"
import { AbsolutePath } from "@opencode/core/schema"
import { Session } from "@opencode/core/session"
import { SessionEnvironment } from "@opencode/core/session/environment"
import { SessionExecution } from "@opencode/core/session/execution"
import { SessionProjector } from "@opencode/core/session/projector"
import { SessionStore } from "@opencode/core/session/store"
import { offlineModels } from "./fixture/models"
import { promptLocationNode } from "./fixture/prompt-location"
import { tmpdirScoped } from "./fixture/tmpdir"
import { testEffect } from "./lib/effect"

const it = testEffect(
  AppNodeBuilder.build(
    LayerNode.group([
      Database.node,
      Bus.node,
      Project.node,
      SessionProjector.node,
      SessionStore.node,
      Job.node,
      SessionEnvironment.node,
      Session.node,
    ]),
    [
      Bus.node.replace(Bus.configured({ persist: true })),
      LocationServiceMap.node.replace(promptLocationNode),
      SessionExecution.node.replace(SessionExecution.noopLayer),
      offlineModels,
    ],
  ),
)

describe("Session.clear", () => {
  it.live("releases the session family's runtime without deleting history", () =>
    tmpdirScoped().pipe(
      Effect.flatMap((tmp) =>
        Effect.gen(function* () {
          const sessions = yield* Session.Service
          const jobs = yield* Job.Service
          const environments = yield* SessionEnvironment.Service
          const location = Location.Ref.make({ directory: AbsolutePath.make(tmp.path) })
          const parent = yield* sessions.create({ location })
          const child = yield* sessions.create({ parentID: parent.id })
          const sibling = yield* sessions.create({ location })

          yield* sessions.environment({ sessionID: parent.id, variables: { KITCHEN: "parent" } })
          yield* sessions.environment({ sessionID: child.id, variables: { KITCHEN: "child" } })
          yield* sessions.environment({ sessionID: sibling.id, variables: { KITCHEN: "sibling" } })

          const latch = yield* Deferred.make<void>()
          const childJob = yield* jobs.start({
            id: child.id,
            type: "subagent",
            recovery: {
              kind: "subagent",
              parentSessionID: parent.id,
              childSessionID: child.id,
              agent: "build",
              description: "child",
            },
            run: Deferred.await(latch).pipe(Effect.as("done")),
          })
          const shell = yield* jobs.start({
            id: "job_clear_parent_shell",
            type: "shell",
            recovery: { kind: "shell", sessionID: parent.id, shellID: "shell_parent", command: "sleep" },
            run: Deferred.await(latch).pipe(Effect.as("done")),
          })
          const foreign = yield* jobs.start({
            id: "job_clear_sibling_shell",
            type: "shell",
            recovery: { kind: "shell", sessionID: sibling.id, shellID: "shell_sibling", command: "sleep" },
            run: Deferred.await(latch).pipe(Effect.as("done")),
          })

          yield* sessions.clear(parent.id)

          expect(yield* sessions.get(parent.id)).toBeDefined()
          expect(yield* sessions.get(child.id)).toBeDefined()
          expect(yield* sessions.get(sibling.id)).toBeDefined()
          expect(yield* environments.get(parent.id)).toBeUndefined()
          expect(yield* environments.get(child.id)).toBeUndefined()
          expect(yield* environments.get(sibling.id)).toEqual({ KITCHEN: "sibling" })
          expect((yield* jobs.get(childJob.id))?.status).toBe("cancelled")
          expect((yield* jobs.get(shell.id))?.status).toBe("cancelled")
          expect((yield* jobs.get(foreign.id))?.status).toBe("running")
        }),
      ),
    ),
  )
})
