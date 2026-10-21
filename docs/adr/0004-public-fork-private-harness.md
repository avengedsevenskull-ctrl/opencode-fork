# ADR 0004 — Keep the public fork clean; archive personal harness privately

- Status: Accepted
- Date: 2026-10-21
- Deciders: fork maintainer

## Context

`avengedsevenskull-ctrl/opencode-fork` is a **public fork** of
`anomalyco/opencode`. GitHub ties a fork's visibility to its upstream; the
fork cannot be made private by itself.

The working tree contains an uncommitted harness migration: tracked project
harness files are deleted, and `.archived-2026-10-20` copies are untracked.
The harness is personal working material, not upstream code. A targeted scan
of the six local commits and the archive found no common credential patterns,
and the archived root `AGENTS.md` and `.opencode/opencode.jsonc` are
byte-identical to the already-public `HEAD`. That is evidence of no new secret
exposure, not a guarantee.

The user already owns a private repository for the global harness and its
archive.

## Decision

- Move the `.archived-2026-10-20` material into the **private harness
  repository** under `archive/2026-10-20-harness/`, preserving paths.
- In the public fork, commit **only the deletions** and create `main` from that
  state. Do not commit the archive, private paths, or personal configuration.
- Do not rewrite public history. Delete the harness going forward; if a real
  credential is later found, rotate it and then decide whether a history
  rewrite is warranted.
- Keep the private handoff and private repository URL in the private harness
  repository, not in the public fork.

## Alternatives considered

- **Make the fork private** — not possible while the upstream is public.
- **New private build repository** — stronger isolation, but unnecessary while
  the custom code can remain public and only the harness is private.
- **Sanitize and publish the archive** — rejected; personal harness does not
  belong in a public fork.
- **Keep the archive only on local disk** — rejected; versioning it in the
  existing private repository is cheap and recoverable.

## Consequences

- The public fork contains code and non-sensitive project docs only.
- The private repository holds the archive and the detailed handoff.
- The public history still contains the harness files from before the
  deletion. The deletion stops future exposure; it does not erase clones.
- `git add -A` is forbidden in this migration: move the archive out first,
  then stage deletions explicitly.

## Guards

- `git status` in the public fork after the archive move shows deletions only,
  then the new docs; no `.archived-2026-10-20` paths.
- The private repository contains `archive/2026-10-20-harness/` and the
  detailed handoff, and is confirmed private before pushing.
- No secret values appear in any public commit message or doc.
