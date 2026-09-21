"""Exact port of OpenCode's permission evaluation from packages/opencode/src/permission/index.ts.

Key functions:
- fromConfig: converts permission config dict to ordered ruleset
- evaluate: findLast across concatenated rulesets (Wildcard.match on both key AND pattern)
- disabled: determines which tool names are stripped from LLM context
- Provenience: tracks where each effective permission value originates from
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from collections import OrderedDict

from .wildcard import match as wildcard_match


class Action(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class Rule:
    permission: str   # e.g. "edit", "mcp-combiner__*", "github__search_code"
    pattern: str      # e.g. "*", "git *", "/absolute/path/*"
    action: Action

    @property
    def is_flat(self) -> bool:
        """True if this rule came from a flat string config (pattern always "*")."""
        return self.pattern == "*"


Ruleset = list[Rule]


# ---------------------------------------------------------------
# fromConfig — matches OpenCode's Permission.fromConfig() exactly
# ---------------------------------------------------------------

def expand_pattern(pattern: str) -> str:
    """Expand tilde and $HOME in patterns (matches OpenCode's expand())."""
    if pattern.startswith("~/"):
        return os.path.expanduser(pattern)
    if pattern == "~":
        return os.path.expanduser("~")
    if pattern.startswith("$HOME/"):
        return os.path.expanduser("~") + pattern[5:]
    if pattern.startswith("$HOME"):
        return os.path.expanduser("~") + pattern[5:]
    return pattern


def from_config(permission: dict) -> Ruleset:
    """Convert a permission config object to an ordered ruleset."""
    ruleset: Ruleset = []
    for key, value in permission.items():
        if isinstance(value, str):
            ruleset.append(Rule(permission=key, action=Action(value), pattern="*"))
            continue
        if isinstance(value, dict):
            for pattern, action in value.items():
                ruleset.append(
                    Rule(permission=key, pattern=expand_pattern(pattern), action=Action(action))
                )
    return ruleset


# ---------------------------------------------------------------
# evaluate — matches OpenCode's Permission.evaluate() exactly
# ---------------------------------------------------------------

def evaluate(tool_name: str, pattern: str, *rulesets: Ruleset) -> Rule:
    """Evaluate permission for a tool+pattern pair across concatenated rulesets.

    Uses findLast: the LAST matching rule wins.
    Defaults to action="ask" if no rule matches.
    """
    flat: Ruleset = [r for rs in rulesets for r in rs]
    for rule in reversed(flat):
        if wildcard_match(tool_name, rule.permission) and wildcard_match(pattern, rule.pattern):
            return rule
    return Rule(action=Action.ASK, permission=tool_name, pattern="*")


# ---------------------------------------------------------------
# disabled — matches OpenCode's Permission.disabled() exactly
# ---------------------------------------------------------------

EDITS = {"edit", "write", "apply_patch"}
READS = {"list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource"}


def disabled(tools: list[str], ruleset: Ruleset) -> set[str]:
    """Return tool names that are stripped from LLM context.

    Three conditions, ALL must be true:
    1. Wildcard.match(tool_name, rule.permission) — permission KEY matches
    2. rule.pattern == "*" — flat form (always true for string-form permissions)
    3. rule.action == "deny"
    """
    result: set[str] = set()
    for tool in tools:
        if tool in EDITS:
            permission_key = "edit"
        elif tool in READS:
            permission_key = "read"
        else:
            permission_key = tool

        rule = _find_last_by_permission(ruleset, permission_key)
        if rule and rule.pattern == "*" and rule.action == Action.DENY:
            result.add(tool)
    return result


def _find_last_by_permission(ruleset: Ruleset, permission_key: str) -> Rule | None:
    for rule in reversed(ruleset):
        if wildcard_match(permission_key, rule.permission):
            return rule
    return None


# ---------------------------------------------------------------
# Provenience — where does each effective permission come from?
# ---------------------------------------------------------------

class Source(str, Enum):
    BUILTIN = "builtin"
    GLOBAL = "global"
    AGENT = "agent"


@dataclass
class Provenience:
    """Per-cell origin tracking for the permission matrix."""
    effective_action: Action
    effective_rule: Rule
    sources: dict[Source, Rule | None] = field(default_factory=dict)

    @property
    def source_label(self) -> str:
        """Which source introduced this permission value (agent overrides global overrides builtin)."""
        for src in (Source.AGENT, Source.GLOBAL, Source.BUILTIN):
            if self.sources.get(src) is not None:
                return src.value
        return "inherited"

    @property
    def is_overridden(self) -> bool:
        """True if this cell has been explicitly set at agent level."""
        return self.source_label == Source.AGENT.value

    @property
    def can_revert(self) -> bool:
        """True if this cell can be reverted to inherited value."""
        return self.is_overridden


def compute_provenience(
    tool_name: str,
    pattern: str,
    builtin_rules: Ruleset,
    global_rules: Ruleset,
    agent_rules: Ruleset,
) -> Provenience:
    """Compute the effective permission AND its provenience.

    Provenience answers: at which SOURCE was this permission value set?
    - BUILTIN: rule comes from built-in agent defaults
    - GLOBAL: rule comes from top-level permission config
    - AGENT: rule comes from agent-level permission override
    """
    combined = builtin_rules + global_rules + agent_rules
    effective = evaluate(tool_name, pattern, *[combined])

    result = Provenience(
        effective_action=effective.action,
        effective_rule=effective,
        sources={
            Source.BUILTIN: _source_rule(tool_name, pattern, builtin_rules),
            Source.GLOBAL: _source_rule(tool_name, pattern, global_rules),
            Source.AGENT: _source_rule(tool_name, pattern, agent_rules),
        },
    )
    return result


def _source_rule(tool_name: str, pattern: str, ruleset: Ruleset) -> Rule | None:
    for rule in reversed(ruleset):
        if wildcard_match(tool_name, rule.permission) and wildcard_match(pattern, rule.pattern):
            return rule
    return None
