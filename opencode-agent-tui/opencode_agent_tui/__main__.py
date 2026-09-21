"""CLI entry point: opencode-agent-tui."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .backup import list_backups, restore_backup, diff_backup
from .app import main as run_tui


def _cmd_rollback(args: argparse.Namespace) -> None:
    backups = list_backups()
    if not backups:
        print("No backups found.")
        return

    if args.list:
        print(f"{'#':>3}  {'Timestamp':<20}  {'Source':<50}  {'Size':>8}")
        print("-" * 90)
        for i, b in enumerate(backups):
            print(f"{i:>3}  {b['timestamp']:<20}  {b['source_file']:<50}  {b['size_bytes'] / 1024:>7.1f} KB")
        return

    if args.restore is not None:
        idx = args.restore
        if idx < 0 or idx >= len(backups):
            print(f"Invalid backup index: {idx}. Use --list to see available backups.")
            sys.exit(1)
        b = backups[idx]
        restored = restore_backup(b["path"])
        if restored:
            print(f"Restored: {restored}")
            print("Restart opencode for changes to take effect.")
        else:
            print("Restore failed.")
            sys.exit(1)
        return

    # Interactive: show backups and prompt
    _cmd_rollback_interactive(backups)


def _cmd_rollback_interactive(backups: list) -> None:
    print(f"{'#':>3}  {'Timestamp':<20}  {'Source':<50}  {'Size':>8}")
    print("-" * 90)
    for i, b in enumerate(backups):
        print(f"{i:>3}  {b['timestamp']:<20}  {b['source_file']:<50}  {b['size_bytes'] / 1024:>7.1f} KB")
    print()
    try:
        choice = input("Restore which backup? [0-{} or q to quit]: ".format(len(backups) - 1))
        if choice.lower() == "q":
            return
        idx = int(choice)
        if idx < 0 or idx >= len(backups):
            print("Invalid index.")
            return
        b = backups[idx]
        restored = restore_backup(b["path"])
        if restored:
            print(f"Restored: {restored}")
            print("Restart opencode for changes to take effect.")
        else:
            print("Restore failed.")
    except (ValueError, KeyboardInterrupt):
        print()
        return


def _cmd_diff(args: argparse.Namespace) -> None:
    backups = list_backups()
    if not backups:
        print("No backups found.")
        return

    idx = args.diff
    if idx < 0 or idx >= len(backups):
        print(f"Invalid backup index: {idx}. Use --rollback --list to see available backups.")
        sys.exit(1)

    b = backups[idx]
    result = diff_backup(b["path"])
    print(result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="opencode-agent-tui — Terminal agent configuration editor for OpenCode",
    )
    parser.add_argument(
        "--rollback", action="store_true",
        help="Show backup list and restore a previous config",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available backups (use with --rollback)",
    )
    parser.add_argument(
        "--restore", type=int, metavar="N",
        help="Restore backup #N directly (use with --rollback)",
    )
    parser.add_argument(
        "--diff", type=int, metavar="N",
        help="Show structured diff between backup #N and current config",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Force tool re-discovery on next TUI launch",
    )
    parser.add_argument(
        "--audit", action="store_true",
        help="Run shadow audit: detect silently-shadowed agent definitions (read-only)",
    )
    parser.add_argument(
        "--audit-json", action="store_true",
        help="Run shadow audit and output machine-readable JSON",
    )
    parser.add_argument(
        "--fix", action="store_true",
        help="Apply safe fixes from an audit (consumes --audit-json output from stdin)",
    )
    parser.add_argument(
        "--migrate-inline", type=str, metavar="NAME",
        help="Convert one inline-only agent to markdown, re-parse, verify, delete inline",
    )
    parser.add_argument(
        "--fix-permissions", action="store_true",
        help="Scan and replace playground_* → browser_* references in all permission blocks",
    )

    args = parser.parse_args()

    if args.rollback:
        _cmd_rollback(args)
    elif args.diff is not None:
        _cmd_diff(args)
    elif args.audit:
        from .audit import run_audit
        print(run_audit("text"))
    elif args.audit_json:
        from .audit import run_audit
        print(run_audit("json"))
    elif args.fix:
        _cmd_fix()
    elif args.migrate_inline:
        _cmd_migrate_inline(args.migrate_inline)
    elif args.fix_permissions:
        _cmd_fix_permissions()
    elif args.refresh:
        import opencode_agent_tui.tool_discovery as td
        td._combiner_cache = None
        td._combiner_cache_ts = 0.0
        run_tui()
    else:
        run_tui()


def _cmd_fix() -> None:
    """Consume audit JSON from stdin and apply safe fixes."""
    import sys as _sys
    import json as _json_lib
    from .audit import Classification

    try:
        report = _json_lib.loads(_sys.stdin.read())
    except (json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: failed to parse audit JSON from stdin: {e}")
        print("Pipe audit output: opencode-agent-tui --audit-json | opencode-agent-tui --fix")
        _sys.exit(1)

    results = report.get("results", [])
    if not results:
        print("No shadowed agents found. Nothing to fix.")
        return

    # Group by classification
    identical = [r for r in results if r["classification"] == Classification.IDENTICAL]
    superset = [r for r in results if r["classification"] == Classification.SUPERSET]
    conflicting = [r for r in results if r["classification"] == Classification.CONFLICTING]
    needs_review = [r for r in results if r["classification"] == Classification.NEEDS_REVIEW]

    print(f"Fixable: {len(identical)} identical + {len(superset)} superset = {len(identical) + len(superset)} safe archives")
    print(f"Needs human review: {len(conflicting)} conflicting + {len(needs_review)} needs_review")
    print()

    if identical or superset:
        # Git clean tree check
        import subprocess
        result = subprocess.run(["git", "diff", "--stat"], capture_output=True, text=True)
        if result.stdout.strip():
            print("WARNING: git working tree is NOT clean. Commit or stash changes first.")
            print("Aborting. No changes made.")
            _sys.exit(1)

        all_safe = identical + superset
        print(f"Safe to archive ({len(all_safe)} agents):")
        for r in all_safe:
            print(f"  {r['agent_name']}")
        print()
        confirm = input("Archive these inline definitions? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

        for r in all_safe:
            _archive_inline_agent(r["agent_name"])

    if conflicting:
        print(f"\n=== Conflicting ({len(conflicting)}) — needs manual resolution ===")
        for r in conflicting:
            print(f"\n  Agent: {r['agent_name']}")
            for c in r.get("conflicts", []):
                print(f"    {c['key']}: inline={c['inline_value']!r}  md={c['md_value']!r}")
        print("\nResolve these manually. Use the TUI to edit the markdown file.")

    if needs_review:
        print(f"\n=== Orphan keys ({len(needs_review)}) — needs manual resolution ===")
        for r in needs_review:
            print(f"\n  Agent: {r['agent_name']}")
            for key in r.get("only_inline", []):
                print(f"    {key}")
        print("\nThese keys exist in inline JSON but NOT in markdown. Resolve manually.")


def _archive_inline_agent(name: str) -> None:
    """Move an inline agent definition from opencode.json to agents/.archived/name.json."""
    import json as _json
    from .config import GLOBAL_CONFIG_DIR, find_all_configs
    from .config_writer import _atomic_write

    sources = find_all_configs()
    for key, path in sources.items():
        if key.startswith("global"):
            cfg = _json.loads(path.read_text())
            if "agent" in cfg and name in cfg["agent"]:
                # Archive
                archive_dir = GLOBAL_CONFIG_DIR / "agents" / ".archived"
                archive_dir.mkdir(parents=True, exist_ok=True)
                archive_path = archive_dir / f"{name}.json"
                _atomic_write(archive_path, _json.dumps(cfg["agent"][name], indent=2))
                # Remove from config
                del cfg["agent"][name]
                _atomic_write(path, _json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
                print(f"  Archived: {name} → agents/.archived/{name}.json")
                return
    print(f"  WARNING: could not find inline definition for '{name}'")


def _cmd_migrate_inline(name: str) -> None:
    """Convert one inline-only agent to markdown, verify, delete inline."""
    import yaml as _yaml
    from .config import load_all_configs, discover_all_agents, GLOBAL_CONFIG_DIR
    from .config_writer import _atomic_write, _parse_frontmatter

    config = load_all_configs()
    agents = discover_all_agents(config)
    agent = next((a for a in agents if a.name == name and a.source_type == "inline" and not a.shadowed), None)
    if agent is None:
        print(f"ERROR: '{name}' is not a safe inline-only agent. Check --audit first.")
        sys.exit(1)

    # Build frontmatter
    fm: dict = {"mode": agent.mode}
    if agent.model:
        fm["model"] = agent.model
    if agent.temperature is not None:
        fm["temperature"] = agent.temperature
    if agent.description:
        fm["description"] = agent.description
    if agent.color:
        fm["color"] = agent.color
    if agent.hidden:
        fm["hidden"] = True
    if agent.permission:
        fm["permission"] = dict(agent.permission)

    yaml_body = _yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content = f"---\n{yaml_body}---\n\n"

    target_dir = GLOBAL_CONFIG_DIR / "agents"
    target_dir.mkdir(parents=True, exist_ok=True)
    filepath = target_dir / f"{name}.md"

    if filepath.exists():
        print(f"ERROR: {filepath} already exists. Aborting.")
        sys.exit(1)

    _atomic_write(filepath, content)
    print(f"Wrote markdown: {filepath}")

    # Re-parse and verify zero semantic diff
    text2 = filepath.read_text()
    fm2, _ = _parse_frontmatter(text2)

    import json as _json
    original_inline = config.get("agent", {}).get(name, {})
    original_keys = set(_json.dumps(original_inline, sort_keys=True, default=str))
    migrated_keys = set(_json.dumps(fm2, sort_keys=True, default=str))

    # Normalize comparison: compare leaf keys from audit module
    from .audit import _leaf_keys
    inline_leaves = _leaf_keys(original_inline)
    md_leaves = _leaf_keys(fm2)

    only_inline = set(inline_leaves) - set(md_leaves)
    only_md = set(md_leaves) - set(inline_leaves)

    if only_inline:
        print(f"WARNING: keys only in inline: {only_inline}")
    if only_md:
        print(f"WARNING: keys only in md: {only_md}")

    conflicts = []
    for k in set(inline_leaves) & set(md_leaves):
        if inline_leaves[k] != md_leaves[k]:
            conflicts.append(k)

    if conflicts:
        print(f"ERROR: value conflicts detected: {conflicts}")
        print("Aborting — delete the markdown file, fix the migration, and retry.")
        filepath.unlink()
        sys.exit(1)

    if only_inline or only_md:
        print(f"WARNING: key set mismatch. Review {filepath} before deleting inline.")
        return

    # All good — delete inline
    sources = find_all_configs()
    for key, path in sources.items():
        if key.startswith("global"):
            cfg = _json.loads(path.read_text())
            if "agent" in cfg and name in cfg["agent"]:
                del cfg["agent"][name]
                _atomic_write(path, _json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
                print(f"Deleted inline definition for '{name}'")
                return
    print(f"No inline definition found for '{name}' (may already be deleted)")


def _cmd_fix_permissions() -> None:
    """Scan all permission blocks for playground_* → browser_*."""
    from .config import load_all_configs, find_all_configs, discover_all_agents
    from .config_writer import _atomic_write
    import json as _json

    config = load_all_configs()
    agents = discover_all_agents(config)
    changes_made = 0

    def _replace_playwright(value):
        nonlocal changes_made
        if isinstance(value, str):
            if "playwright_" in value:
                changes_made += 1
                return value.replace("playwright_", "browser_")
            return value
        if isinstance(value, dict):
            new = {}
            for k, v in value.items():
                new_k = k.replace("playwright_", "browser_")
                new_v = _replace_playwright(v)
                if new_k != k:
                    changes_made += 1
                new[new_k] = new_v
            return new
        if isinstance(value, list):
            return [_replace_playwright(v) for v in value]
        return value

    # Fix global permissions
    global_perms = config.get("permission", {})
    new_perms = _replace_playwright(dict(global_perms))

    # Fix agent permissions (only markdown agents)
    for agent in agents:
        if agent.source_type == "markdown" and agent.source_path:
            text = Path(agent.source_path).read_text(encoding="utf-8")
            if "playwright_" not in text:
                continue
            new_text = text.replace("playwright_", "browser_")
            _atomic_write(Path(agent.source_path), new_text)
            changes_made += text.count("playwright_")
            print(f"  Fixed {agent.source_path}: {text.count('playwright_')} references")

    print(f"\nTotal replacements: {changes_made}")
    if changes_made > 0:
        print("Restart opencode for changes to take effect.")


if __name__ == "__main__":
    main()
