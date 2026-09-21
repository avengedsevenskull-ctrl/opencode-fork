"""Backup system: automatic snapshots before saves, rollback, diff, retention."""

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BACKUP_DIR = Path.home() / ".config" / "opencode" / "backups"
MAX_BACKUPS = 20
SECRET_PATTERNS = ("apiKey", "token", "secret", "password", "key")


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def backup_config(filepath: Path) -> Path | None:
    """Create a timestamped backup of a config file. Returns backup path or None."""
    if not filepath.exists():
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    safe_name = str(filepath).replace("/", "_").lstrip("_")
    backup_path = BACKUP_DIR / f"{timestamp}__{safe_name}"

    shutil.copy2(filepath, backup_path)
    os.chmod(backup_path, 0o600)

    # Prune old backups per-source-file
    _prune_backups(filepath)

    return backup_path


def _prune_backups(filepath: Path) -> None:
    """Keep only the last MAX_BACKUPS backups per source file."""
    safe_name = str(filepath).replace("/", "_").lstrip("_")
    backups = sorted(
        [b for b in BACKUP_DIR.iterdir() if b.name.endswith(f"__{safe_name}")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[MAX_BACKUPS:]:
        old.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def list_backups(filepath: Path | None = None) -> list[dict[str, Any]]:
    """List all backups, optionally filtered by source file path.

    Returns list of {timestamp, path, source_file, size_bytes} dicts sorted newest-first.
    """
    if not BACKUP_DIR.exists():
        return []

    backups = sorted(BACKUP_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    result: list[dict[str, Any]] = []

    for b in backups:
        parts = b.name.split("__", 1)
        if len(parts) != 2:
            continue
        ts, source = parts
        if filepath is not None and source != str(filepath).replace("/", "_").lstrip("_"):
            continue
        result.append({
            "timestamp": ts,
            "path": b,
            "source_file": source.replace("_", "/"),
            "size_bytes": b.stat().st_size,
        })
    return result


def restore_backup(backup_path: Path) -> str | None:
    """Restore a backup. Returns source file path on success, None on failure."""
    parts = backup_path.name.split("__", 1)
    if len(parts) != 2:
        return None
    source_name = parts[1].replace("_", "/")
    source_path = Path("/" + source_name)

    # First backup the CURRENT state (safety net)
    backup_config(source_path)

    # Restore
    shutil.copy2(backup_path, source_path)
    return str(source_path)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def redact_secrets(obj: Any) -> Any:
    """Recursively redact secret-like keys from a dict."""
    if isinstance(obj, dict):
        return {
            k: "***REDACTED***" if any(p in k.lower() for p in SECRET_PATTERNS)
            else redact_secrets(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_secrets(v) for v in obj]
    return obj


def diff_backup(backup_path: Path, current_path: Path | None = None) -> str:
    """Show a structured diff between a backup and the current config.

    Returns a human-readable diff string. Redacts secrets.
    """
    if not backup_path.exists():
        return f"Backup not found: {backup_path}"

    # If no current path given, derive from backup name
    if current_path is None:
        parts = backup_path.name.split("__", 1)
        if len(parts) == 2:
            source_name = parts[1].replace("_", "/")
            current_path = Path("/" + source_name)

    if current_path is None or not current_path.exists():
        return f"Current config not found: {current_path}"

    try:
        backup_data = json.loads(backup_path.read_text())
        current_data = json.loads(current_path.read_text())
    except (json.JSONDecodeError, ValueError) as e:
        return f"Cannot parse config: {e}"

    # Redact secrets before diff display
    backup_data = redact_secrets(backup_data)
    current_data = redact_secrets(current_data)

    lines: list[str] = []
    lines.append(f"Backup: {backup_path.name}")
    lines.append(f"Current: {current_path}")
    lines.append("")

    # Agent-level diffs
    backup_agents = backup_data.get("agent", {})
    current_agents = current_data.get("agent", {})
    all_agent_names = sorted(set(backup_agents) | set(current_agents))

    for name in all_agent_names:
        b = backup_agents.get(name, {})
        c = current_agents.get(name, {})

        # Permission diff
        b_perms = b.get("permission", {})
        c_perms = c.get("permission", {})
        if b_perms != c_perms:
            added = set(c_perms) - set(b_perms)
            removed = set(b_perms) - set(c_perms)
            changed_keys = []
            for k in set(b_perms) & set(c_perms):
                if b_perms[k] != c_perms[k]:
                    changed_keys.append(f"  {k}: {b_perms[k]} → {c_perms[k]}")
            if added:
                lines.append(f"[{name}] +added permissions: {sorted(added)}")
            if removed:
                lines.append(f"[{name}] -removed permissions: {sorted(removed)}")
            for ck in changed_keys:
                lines.append(f"[{name}] ~changed: {ck}")

        # Config field diffs
        for field in ("model", "temperature", "mode", "color"):
            if b.get(field) != c.get(field):
                lines.append(f"[{name}] {field}: {b.get(field)} → {c.get(field)}")

    # Top-level permission diff
    b_top = backup_data.get("permission", {})
    c_top = current_data.get("permission", {})
    if b_top != c_top:
        added_top = set(c_top) - set(b_top)
        removed_top = set(b_top) - set(c_top)
        if added_top:
            lines.append(f"[global] +added: {sorted(added_top)}")
        if removed_top:
            lines.append(f"[global] -removed: {sorted(removed_top)}")

    if len(lines) <= 3:
        lines.append("No changes detected.")

    return "\n".join(lines)
