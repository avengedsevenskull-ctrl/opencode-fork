"""Shadow audit: recursive leaf-key classifier for detecting silently-shadowed
agent definitions. Compares inline JSON definitions against markdown frontmatter
at the leaf-key level, producing a classification report.

Output categories:
 - identical:  every leaf key exists in both, identical values
 - superset:   markdown has strictly more keys than inline; all inline keys match
 - conflicting: same key exists in both but DIFFERENT values (most dangerous)
 - needs_review: inline has keys that markdown does NOT have (orphan fields)

Schema-filtered: only KNOWN_AGENT_FIELDS are compared. Non-config frontmatter
fields (description, tags, etc.) are excluded from comparison.
"""

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import (
    AgentDef, GLOBAL_CONFIG_DIR, KNOWN_AGENT_FIELDS,
    discover_all_agents, load_all_configs, find_all_configs,
    discover_json_agents, discover_markdown_agents,
)
from .permissions import Rule, Ruleset, from_config


# ---------------------------------------------------------------------------
# Leaf-key extraction
# ---------------------------------------------------------------------------

def _leaf_keys(obj: dict, prefix: str = "", schema_only: bool = True) -> dict[str, Any]:
    """Recursively flatten a nested dict to leaf-key paths with values.

    Returns { "permission.edit": "deny", "permission.bash.git_*": "allow", ... }
    """
    result: dict[str, Any] = {}
    for key, value in obj.items():
        # Skip non-schema fields if filtering
        if schema_only and key not in KNOWN_AGENT_FIELDS:
            continue
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and not _is_permission_leaf(value):
            result.update(_leaf_keys(value, full_key, schema_only))
        else:
            # Normalize value for comparison
            result[full_key] = _normalize_value(value)
    return result


def _is_permission_leaf(obj: dict) -> bool:
    """Check if a dict looks like a permission rule set (string values)
    rather than a nested config object. Permission objects like
    {"git *": "allow", "*": "deny"} have string values — these ARE leaves."""
    if not obj:
        return False
    # If all values are strings, this is a flat permission map (leaf)
    return all(isinstance(v, (str, bool, int, float, type(None))) for v in obj.values())


def _normalize_value(value: Any) -> Any:
    """Normalize a value for comparison: sort lists, convert bools, etc."""
    if isinstance(value, list):
        return sorted(deepcopy(value))
    if isinstance(value, bool):
        return bool(value)
    return value


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

class Classification:
    IDENTICAL = "identical"
    SUPERSET = "superset"
    CONFLICTING = "conflicting"
    NEEDS_REVIEW = "needs_review"


def classify_agent(agent: AgentDef, merged_config: dict, all_agents: list[AgentDef]) -> dict[str, Any]:
    """Classify a single shadowed agent by comparing inline vs markdown.

    Args:
        agent: the SHADOWED inline agent (source_type="inline", shadowed=True)
        merged_config: full merged opencode config dict
        all_agents: all discovered agents (to find the matching markdown version)
    """
    # Get inline definition
    inline_agent = merged_config.get("agent", {}).get(agent.name, {})
    inline_keys = _leaf_keys(inline_agent)

    # Find the matching markdown agent that shadows this inline one
    md_agent = next(
        (a for a in all_agents
         if a.name == agent.name and a.source_type == "markdown" and a.source_path),
        None,
    )
    md_frontmatter: dict = {}
    if md_agent and md_agent.source_path:
        path = Path(md_agent.source_path)
        if path.exists():
            text = path.read_text(encoding="utf-8")
            from .config import _parse_frontmatter
            fm, _ = _parse_frontmatter(text)
            md_frontmatter = dict(fm)
    md_keys = _leaf_keys(md_frontmatter)

    # Sets of leaf key paths
    inline_set = set(inline_keys.keys())
    md_set = set(md_keys.keys())
    common = inline_set & md_set
    only_inline = inline_set - md_set
    only_md = md_set - inline_set

    # Check for value conflicts in common keys
    conflicts: list[dict] = []
    for key in sorted(common):
        if inline_keys[key] != md_keys[key]:
            conflicts.append({
                "key": key,
                "inline_value": inline_keys[key],
                "md_value": md_keys[key],
            })

    # Determine classification
    if not only_inline and not only_md and not conflicts:
        classification = Classification.IDENTICAL
    elif not only_inline and not conflicts and only_md:
        classification = Classification.SUPERSET
    elif conflicts or only_inline:
        if conflicts:
            classification = Classification.CONFLICTING
        else:
            classification = Classification.NEEDS_REVIEW
    else:
        # md has extra keys but no conflicts and no orphans — superset
        classification = Classification.SUPERSET

    return {
        "agent_name": agent.name,
        "classification": classification,
        "inline_leaf_count": len(inline_keys),
        "md_leaf_count": len(md_keys),
        "common_keys": len(common),
        "only_inline": sorted(only_inline),
        "only_md": sorted(only_md),
        "conflicts": conflicts,
        "inline_source": "opencode.json:agent." + agent.name,
        "md_source": str(agent.source_path) if agent.source_path else None,
        "safe_to_archive": classification in (Classification.IDENTICAL, Classification.SUPERSET),
    }


def run_audit(output_format: str = "text") -> str:
    """Run the full shadow audit against the current config.

    Args:
        output_format: "text" for human-readable, "json" for machine-readable

    Returns:
        Report string.
    """
    config = load_all_configs()
    agents = discover_all_agents(config)

    shadowed = [a for a in agents if a.shadowed and a.source_type == "inline"]
    inline_only = [a for a in agents if a.source_type == "inline" and not a.shadowed]

    results: list[dict] = []
    for agent in shadowed:
        results.append(classify_agent(agent, config, agents))

    summary = {
        "total_agents": len(agents),
        "shadowed": len(shadowed),
        "inline_only": len(inline_only),
        "results": results,
        "counts": {
            "identical": sum(1 for r in results if r["classification"] == Classification.IDENTICAL),
            "superset": sum(1 for r in results if r["classification"] == Classification.SUPERSET),
            "conflicting": sum(1 for r in results if r["classification"] == Classification.CONFLICTING),
            "needs_review": sum(1 for r in results if r["classification"] == Classification.NEEDS_REVIEW),
        },
    }

    if output_format == "json":
        return json.dumps(summary, indent=2, default=str)

    # Text output
    lines: list[str] = []
    lines.append(f"Shadow Audit: {summary['shadowed']} shadowed · {summary['inline_only']} inline-only")
    lines.append(f"  ≡ identical:  {summary['counts']['identical']}")
    lines.append(f"  ⊇ superset:   {summary['counts']['superset']}")
    lines.append(f"  ⚡ conflicting: {summary['counts']['conflicting']}")
    lines.append(f"  ⚠ needs_review: {summary['counts']['needs_review']}")
    lines.append("")

    for r in results:
        symbol = {"identical": "≡", "superset": "⊇", "conflicting": "⚡", "needs_review": "⚠"}
        sym = symbol.get(r["classification"], "?")
        lines.append(f"  {sym} {r['agent_name']:25s} {r['classification']:12s}  "
                     f"inline={r['inline_leaf_count']:3d}  md={r['md_leaf_count']:3d}  "
                     f"{'SAFE' if r['safe_to_archive'] else '⚠ REVIEW'}")
        if r["conflicts"]:
            for c in r["conflicts"]:
                lines.append(f"     ⚡ {c['key']}: {c['inline_value']!r} → {c['md_value']!r}")
        if r["only_inline"]:
            lines.append(f"     ⚠ orphan keys: {', '.join(r['only_inline'])}")

    return "\n".join(lines)
