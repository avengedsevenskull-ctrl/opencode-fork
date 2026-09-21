"""Config write layer: atomic file writes, concurrency checks, scope routing,
markdown frontmatter editing, pre-save validation."""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import AgentDef, KNOWN_AGENT_FIELDS, _parse_frontmatter


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically (temp file + rename)."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        dir=path.parent,
        prefix="." + path.name + ".",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    )
    try:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        os.unlink(tmp.name)
        raise


# ---------------------------------------------------------------------------
# Concurrency check
# ---------------------------------------------------------------------------

@dataclass
class ConcurrencyCheck:
    path: Path
    mtime: float
    sha256: str


def snapshot_file(path: Path) -> ConcurrencyCheck | None:
    """Take snapshot of a file for later concurrency check."""
    if not path.exists():
        return None
    stat = path.stat()
    content = path.read_bytes()
    return ConcurrencyCheck(
        path=path,
        mtime=stat.st_mtime,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def check_stale(snapshot: ConcurrencyCheck | None) -> str | None:
    """Return error message if file changed since snapshot, or None if clean."""
    if snapshot is None:
        return None
    if not snapshot.path.exists():
        return None  # file was deleted — proceed
    current_stat = snapshot.path.stat()
    if current_stat.st_mtime != snapshot.mtime:
        return f"{snapshot.path.name} was modified on disk since loading. Reload or overwrite?"
    current_content = snapshot.path.read_bytes()
    current_hash = hashlib.sha256(current_content).hexdigest()
    if current_hash != snapshot.sha256:
        return f"{snapshot.path.name} content changed on disk since loading. Reload or overwrite?"
    return None


# ---------------------------------------------------------------------------
# Write targets
# ---------------------------------------------------------------------------

@dataclass
class WriteTarget:
    """Describes where to write an edit."""
    path: Path          # file to write to
    kind: str           # "json" or "markdown"
    agent_name: str     # agent being edited


def resolve_write_target(agent: AgentDef, sources: dict[str, Path]) -> WriteTarget | None:
    """Determine where edits for an agent should be written.

    Rules:
    - Markdown-defined agents → write to .md file (frontmatter)
    - Inline-only agents → write to opencode.json
    - Shadowed inline agents → CANNOT write (markdown wins), return None
    """
    if agent.shadowed:
        return None  # Editing this copy has zero effect

    if agent.source_type == "markdown" and agent.source_path:
        return WriteTarget(
            path=Path(agent.source_path),
            kind="markdown",
            agent_name=agent.name,
        )

    # Inline: write to the project config if it exists, otherwise global
    project_paths = [p for k, p in sources.items() if k.startswith("project")]
    if project_paths:
        target_path = project_paths[0]  # prefer project-level
    else:
        global_paths = [p for k, p in sources.items() if k.startswith("global")]
        target_path = global_paths[0] if global_paths else None

    if target_path is None:
        return None

    return WriteTarget(
        path=target_path,
        kind="jsonc" if target_path.suffix == ".jsonc" else "json",
        agent_name=agent.name,
    )


# ---------------------------------------------------------------------------
# Markdown frontmatter editing
# ---------------------------------------------------------------------------

def write_markdown_agent(
    filepath: Path,
    agent_name: str,
    updates: dict[str, Any],
) -> str:
    """Edit an agent's YAML frontmatter in a markdown file.

    Only edits KNOWN_AGENT_FIELDS (model, temperature, mode, permission, tools, etc.).
    Unknown frontmatter keys and the markdown body are preserved byte-for-byte.

    Returns a preview string of what changed.
    """
    text = filepath.read_text(encoding="utf-8")
    original = text
    fm, body = _parse_frontmatter(text)
    if not fm:
        return f"WARNING: no frontmatter found in {filepath}"

    changed: list[str] = []
    for key, value in updates.items():
        old = fm.get(key)
        if old != value:
            changed.append(f"  {key}: {old!r} → {value!r}")
            fm[key] = value

    if not changed:
        return "No changes detected"

    # Re-serialize frontmatter preserving original YAML style
    new_fm = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    new_text = f"---\n{new_fm}---\n\n{body}\n"
    _atomic_write(filepath, new_text)

    return "\n".join(changed)


# ---------------------------------------------------------------------------
# JSON config editing (for inline agents)
# ---------------------------------------------------------------------------

def write_json_agent_permission(
    filepath: Path,
    agent_name: str,
    permission_updates: dict[str, Any],
) -> str:
    """Edit an agent's permission block in a JSON config file.

    Preserves all other config keys untouched. Uses JSON (not JSONC) for write-back.
    """
    text = filepath.read_text(encoding="utf-8")
    cfg = json.loads(text)

    if "agent" not in cfg or agent_name not in cfg["agent"]:
        return f"ERROR: agent '{agent_name}' not found in {filepath}"

    agent_block = cfg["agent"][agent_name]
    changes: list[str] = []

    if "permission" in permission_updates:
        old_perms = agent_block.get("permission", {})
        agent_block["permission"] = permission_updates["permission"]
        if old_perms != permission_updates["permission"]:
            changes.append("  permission updated")

    for key in ("model", "variant", "temperature", "mode", "color",
                "hidden", "disable", "steps", "description"):
        if key in permission_updates:
            old = agent_block.get(key)
            new_val = permission_updates[key]
            if old != new_val:
                changes.append(f"  {key}: {old!r} → {new_val!r}")
            agent_block[key] = new_val

    if not changes:
        return "No changes detected"

    new_text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    _atomic_write(filepath, new_text)

    return "\n".join(changes)


# ---------------------------------------------------------------------------
# Pre-save validation
# ---------------------------------------------------------------------------

def validate_permission_rules(permission: dict) -> list[str]:
    """Validate permission config before saving. Returns list of errors."""
    errors: list[str] = []
    valid_actions = {"allow", "ask", "deny"}

    for key, value in permission.items():
        if isinstance(value, str):
            if value not in valid_actions:
                errors.append(f"Invalid action '{value}' for permission key '{key}'")
        elif isinstance(value, dict):
            for pattern, action in value.items():
                if action not in valid_actions:
                    errors.append(
                        f"Invalid action '{action}' for '{key}' pattern '{pattern}'"
                    )
        else:
            errors.append(f"Invalid permission value type for '{key}': {type(value).__name__}")
    return errors


def validate_agent_updates(updates: dict) -> list[str]:
    """Validate agent updates before writing."""
    errors: list[str] = []
    if "temperature" in updates:
        t = updates["temperature"]
        if not isinstance(t, (int, float)) or t < 0 or t > 2:
            errors.append(f"Temperature must be 0-2, got {t}")
    if "mode" in updates and updates["mode"] not in ("primary", "subagent", "all"):
        errors.append(f"Mode must be primary/subagent/all, got {updates['mode']}")
    if "permission" in updates:
        errors.extend(validate_permission_rules(updates["permission"]))
    return errors


# ---------------------------------------------------------------------------
# Full agent save (routes to correct target)
# ---------------------------------------------------------------------------

@dataclass
class SaveResult:
    success: bool
    changes: str  # human-readable list of changes
    errors: list[str]
    target_path: str | None
    requires_restart: bool = True  # opencode does not hot-reload


def save_agent(
    agent: AgentDef,
    updates: dict[str, Any],
    sources: dict[str, Path],
) -> SaveResult:
    """Save agent configuration, routing to the correct target file.

    Returns a SaveResult describing what happened.
    """
    target = resolve_write_target(agent, sources)

    if target is None:
        if agent.shadowed:
            return SaveResult(
                success=False,
                changes="",
                errors=[
                    f"Agent '{agent.name}' is defined in {agent.source_type} "
                    f"but SILENTLY OVERRIDDEN by a markdown definition. "
                    f"Edits to this source have ZERO effect. Edit the markdown file instead."
                ],
                target_path=None,
            )
        return SaveResult(
            success=False,
            changes="",
            errors=[f"No writable target found for agent '{agent.name}'"],
            target_path=None,
        )

    # Pre-save validation
    errors = validate_agent_updates(updates)
    if errors:
        return SaveResult(
            success=False,
            changes="",
            errors=errors,
            target_path=str(target.path),
        )

    # Filter updates: separate permission from config fields
    permission_updates: dict = {}
    config_updates: dict = {}
    for k, v in updates.items():
        if k in ("permission", "tools"):
            permission_updates[k] = v
        elif k in KNOWN_AGENT_FIELDS:
            config_updates[k] = v

    changes: str

    if target.kind == "markdown":
        # Write to markdown frontmatter
        all_updates = {**config_updates, **permission_updates}
        changes = write_markdown_agent(target.path, agent.name, all_updates)
    else:
        # Write to JSON config
        all_updates = {**config_updates, "permission": permission_updates.get("permission", {})}
        changes = write_json_agent_permission(target.path, agent.name, all_updates)

    return SaveResult(
        success=not changes.startswith("ERROR"),
        changes=changes,
        errors=[],
        target_path=str(target.path),
        requires_restart=True,
    )
