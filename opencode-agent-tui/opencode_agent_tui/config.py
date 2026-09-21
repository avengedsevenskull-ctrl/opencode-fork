"""Config read layer: loads opencode.json/jsonc, discovers agents from inline JSON
and markdown files, parses YAML frontmatter, detects name collisions.

Mirrors OpenCode's config loading order:
1. Global ~/.config/opencode/opencode.json[c]
2. Project ./opencode.json[c], ./.opencode/opencode.json[c]
3. Markdown agents from {agent,agents}/**/*.md (global then project, merged on top)
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Config file discovery
# ---------------------------------------------------------------------------

GLOBAL_CONFIG_DIR = Path.home() / ".config" / "opencode"
CONFIG_CANDIDATES = ["opencode.json", "opencode.jsonc"]


def find_config_files(base_dir: Path) -> list[Path]:
    """Find opencode.json[c] files, returning in load order (sorted by name)."""
    result: list[Path] = []
    for name in sorted(CONFIG_CANDIDATES):
        candidate = base_dir / name
        if candidate.exists():
            result.append(candidate)
    return result


def find_all_configs(project_dir: Path | None = None) -> dict[str, Path]:
    """Find all config files: global + optional project.

    Returns dict of {scope: path}.
    """
    sources: dict[str, Path] = {}

    # Global
    for p in find_config_files(GLOBAL_CONFIG_DIR):
        sources[f"global:{p.name}"] = p

    # Project
    if project_dir:
        project_dotfile = project_dir / ".opencode"
        for base in (project_dir, project_dotfile):
            if not base.exists():
                continue
            for p in find_config_files(base):
                # Prefer local (project-level) key for the opencode.json entry
                key = "project" if p.parent == project_dir else f"project:{p.parent.name}"
                sources[key] = p

    return sources


# ---------------------------------------------------------------------------
# JSON / JSONC parsing
# ---------------------------------------------------------------------------

def parse_jsonc(text: str) -> dict:
    """Parse a JSON or JSONC (JSON-with-comments) file to a Python dict.

    Strips // and /* */ comments. Preserves trailing commas (Python is lenient).
    """
    # Remove // line comments (but not in strings)
    no_line = re.sub(r'(?<!:)\s*//.*$', '', text, flags=re.MULTILINE)
    # Remove /* block comments */
    no_block = re.sub(r'/\*.*?\*/', '', no_line, flags=re.DOTALL)
    return json.loads(no_block)


def load_config_file(path: Path) -> dict:
    """Load a config file, auto-detecting JSON vs JSONC."""
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonc":
        return parse_jsonc(raw)
    return json.loads(raw)


def load_all_configs(project_dir: Path | None = None) -> dict:
    """Load and deep-merge all config sources into a single dict.

    Returns the merged config dict.
    """
    sources = find_all_configs(project_dir)
    merged: dict = {}
    for scope, path in sources.items():
        try:
            cfg = load_config_file(path)
            merged = _deep_merge(merged, cfg)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"WARNING: failed to parse {path}: {e}")
    return merged


# ---------------------------------------------------------------------------
# Deep merge (mirrors remeda mergeDeep: later sources override earlier)
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dicts. override values win on key conflict."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Agent discovery
# ---------------------------------------------------------------------------

@dataclass
class AgentDef:
    name: str
    mode: str  # primary, subagent, all
    model: str | None = None
    variant: str | None = None
    temperature: float | None = None
    color: str | None = None
    hidden: bool = False
    disabled: bool = False
    steps: int | None = None
    description: str | None = None
    permission: dict = field(default_factory=dict)
    tools: dict = field(default_factory=dict)
    prompt: str | None = None
    options: dict = field(default_factory=dict)

    # Provenance
    source_type: str = "inline"  # "inline" or "markdown"
    source_path: str | None = None  # path to .md file if markdown
    shadowed: bool = False  # True if another definition silently overrides this one


KNOWN_AGENT_FIELDS = {
    "name", "model", "variant", "description", "mode", "hidden",
    "color", "steps", "options", "permission", "disable",
    "temperature", "top_p", "tools",
}


def _parse_agent_frontmatter(frontmatter: dict, body: str, source_path: str) -> AgentDef:
    """Convert YAML frontmatter dict to AgentDef."""
    name = frontmatter.get("name", Path(source_path).stem)
    known = {k: v for k, v in frontmatter.items() if k in KNOWN_AGENT_FIELDS}
    unknown = {k: v for k, v in frontmatter.items() if k not in KNOWN_AGENT_FIELDS and k != "name"}

    return AgentDef(
        name=name,
        mode=known.get("mode", "subagent"),
        model=known.get("model"),
        variant=known.get("variant"),
        temperature=known.get("temperature"),
        color=known.get("color"),
        hidden=bool(known.get("hidden", False)),
        disabled=bool(known.get("disable", False)),
        steps=known.get("steps"),
        description=known.get("description"),
        permission=known.get("permission", {}),
        tools=known.get("tools", {}),
        prompt=body.strip() if body.strip() else None,
        options=unknown,  # unknown fields silently land in options
        source_type="markdown",
        source_path=source_path,
    )


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown text. Returns (frontmatter_dict, body).

    Uses frontmatter library first; falls back to regex-based extraction for
    files with unquoted colons in description fields that break PyYAML.
    """
    import frontmatter as _fm
    import re as _re

    if not text.startswith("---"):
        return {}, text

    # Try yaml-based parsing first
    try:
        post = _fm.loads(text)
        fm = dict(post.metadata) if post.metadata else {}
        body = post.content.strip() if post.content else ""
        return fm, body
    except Exception:
        pass

    # Fallback: regex-based extraction of known schema fields
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    yaml_block = parts[1]
    body = parts[2].strip()

    fm: dict = {}
    # Top-level scalars
    for field in ("mode", "model", "variant", "color", "name"):
        m = _re.search(rf"^{field}:\s*(.+)$", yaml_block, _re.MULTILINE)
        if m:
            val = m.group(1).strip().strip('"').strip("'")
            if val:
                fm[field] = val

    for field in ("temperature", "steps"):
        m = _re.search(rf"^{field}:\s*([0-9.]+)", yaml_block, _re.MULTILINE)
        if m:
            fm[field] = float(m.group(1)) if "." in m.group(1) else int(m.group(1))

    for field in ("hidden", "disable"):
        m = _re.search(rf"^{field}:\s*(true|false)", yaml_block, _re.MULTILINE)
        if m:
            fm[field] = m.group(1).lower() == "true"

    # Description: capture everything between "description:" and the next top-level key
    m = _re.search(r"^description:\s*(.+?)(?=\n\w+:\s|\Z)", yaml_block, _re.DOTALL)
    if m:
        fm["description"] = m.group(1).strip()

    # Permission block: capture the permission section
    perm_match = _re.search(r"^permission:\s*\n((?:\s{2,}.+\n?)+)", yaml_block, _re.MULTILINE)
    if perm_match:
        perm_text = perm_match.group(1)
        fm["permission"] = _parse_permission_yaml(perm_text)

    # Tools block
    tools_match = _re.search(r"^tools:\s*\n((?:\s{2,}.+\n?)+)", yaml_block, _re.MULTILINE)
    if tools_match:
        tools_text = tools_match.group(1)
        fm["tools"] = {}
        for t_line in tools_text.split("\n"):
            tm = _re.match(r"\s{2,}(\S+):\s*(.+)$", t_line)
            if tm:
                fm["tools"][tm.group(1)] = tm.group(2).strip() == "true"

    return fm, body


def _parse_permission_yaml(text: str) -> dict:
    """Parse a permission block from YAML frontmatter, handling both flat and nested forms."""
    import re as _re
    perms: dict = {}
    for line in text.split("\n"):
        # Indented sub-key: "  git *: allow"
        subm = _re.match(r"\s{4,}(\S.+?):\s*(.+)$", line)
        if subm:
            # Find the parent key (last key with 2-space indent)
            parent = None
            for prev in perms.keys():
                if prev and not prev.startswith(" "):
                    parent = prev
            if parent and isinstance(perms.get(parent), dict):
                perms[parent][subm.group(1).strip()] = subm.group(2).strip()
            continue

        # Top-level key: "  edit: deny"
        m = _re.match(r"\s{2,}(\S+):\s*(.+)$", line)
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip().strip('"').strip("'")
            if val in ("allow", "ask", "deny"):
                perms[key] = val
            elif val == "":
                perms[key] = {}  # Start of a nested block
    return perms


AGENT_DIRS = ["agent", "agents"]


def discover_markdown_agents(config_dir: Path) -> list[AgentDef]:
    """Discover agents from markdown files in a config directory.

    Mirrors OpenCode's ConfigAgent.load(dir) which scans {agent,agents}/**/*.md.
    Path may contain multiple directory levels (e.g. agent/special/builder.md).
    """
    agents: list[AgentDef] = []
    seen: set[str] = set()

    for dir_name in AGENT_DIRS:
        agent_dir = config_dir / dir_name
        if not agent_dir.is_dir():
            continue
        # Glob: agent/**/*.md (recursive)
        for filepath in sorted(agent_dir.rglob("*.md")):
            text = filepath.read_text(encoding="utf-8")
            fm, body = _parse_frontmatter(text)
            agent = _parse_agent_frontmatter(fm, body, str(filepath))
            if agent.name in seen:
                continue
            seen.add(agent.name)
            agents.append(agent)
    return agents


def discover_json_agents(config: dict, source: str) -> list[AgentDef]:
    """Extract agent definitions from the 'agent' key in a merged config dict."""
    agents: list[AgentDef] = []
    agent_section = config.get("agent", {})
    if not isinstance(agent_section, dict):
        return agents

    for name, value in agent_section.items():
        if not isinstance(value, dict):
            continue
        agent = AgentDef(
            name=name,
            mode=value.get("mode", "subagent"),
            model=value.get("model"),
            variant=value.get("variant"),
            temperature=value.get("temperature"),
            color=value.get("color"),
            hidden=value.get("hidden", False),
            disabled=value.get("disable", False),
            steps=value.get("steps"),
            description=value.get("description"),
            permission=value.get("permission", {}),
            tools=value.get("tools", {}),
            prompt=value.get("prompt"),
            options={k: v for k, v in value.items()
                     if k not in KNOWN_AGENT_FIELDS and k != "prompt"},
            source_type="inline",
            source_path=source,
        )
        agents.append(agent)
    return agents


def discover_all_agents(
    config: dict,
    global_config_dir: Path = GLOBAL_CONFIG_DIR,
    project_dir: Path | None = None,
) -> list[AgentDef]:
    """Discover all agents from all sources, detecting and marking collisions.

    Load order (mirrors OpenCode):
    1. Inline JSON definitions from merged config
    2. Global markdown agents
    3. Project markdown agents (loaded last via mergeDeep, win conflicts)
    """
    # 1. Inline agents (from merged config, scope already handled by deep-merge)
    inline_agents = discover_json_agents(config, "merged_config")
    inline_map = {a.name: a for a in inline_agents}

    # 2. Global markdown agents
    global_md = discover_markdown_agents(global_config_dir)

    # 3. Project markdown agents
    project_md: list[AgentDef] = []
    if project_dir:
        dotcode = project_dir / ".opencode"
        if dotcode.exists():
            project_md = discover_markdown_agents(dotcode)
        # Also check top-level
        top_level = discover_markdown_agents(project_dir)
        # Avoid duplicates: project-level dotcode wins over top-level per opencode semantics
        project_names = {a.name for a in project_md}
        for a in top_level:
            if a.name not in project_names:
                project_md.append(a)

    # Merge: markdown wins over inline (mergeDeep semantics)
    all_md = global_md + project_md
    result: list[AgentDef] = []

    # Inline agents that are NOT shadowed by markdown
    md_names = {a.name for a in all_md}
    for a in inline_agents:
        if a.name in md_names:
            a.shadowed = True
        result.append(a)

    # Markdown agents — later definitions win (project over global)
    # Use reverse to simulate mergeDeep: first occurrence wins keep, later overwrites
    # Actually: mergeDeep(target, source) means source wins. In load order,
    # global_md is loaded first, then project_md. So project_md wins.
    md_by_name: dict[str, AgentDef] = {}
    for a in all_md:
        if a.name in md_by_name:
            # Mark the earlier definition as shadowed
            md_by_name[a.name].shadowed = True
        md_by_name[a.name] = a

    for a in md_by_name.values():
        result.append(a)

    # Sort for display
    result.sort(key=lambda a: a.name)
    return result


# ---------------------------------------------------------------------------
# Collision warnings
# ---------------------------------------------------------------------------

def get_collision_summary(agents: list[AgentDef]) -> list[str]:
    """Return human-readable warnings for any silently-shadowed agents."""
    warnings: list[str] = []
    for a in agents:
        if a.shadowed:
            source_desc = "markdown file" if a.source_type == "markdown" else "inline JSON"
            warnings.append(
                f"Agent '{a.name}' defined in {a.source_path or source_desc} is silently "
                f"overridden by another definition (markdown wins over inline JSON). "
                f"Edits to this definition will have NO effect."
            )
    return warnings
