"""Tool discovery: live combiner tools, built-in registry, plugin tools, caching."""

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Built-in tool registry (matches opencode's built-in tools)
# ---------------------------------------------------------------------------

BUILTIN_TOOLS: dict[str, str] = {
    "read": "Read a file or directory from the local filesystem",
    "edit": "Performs exact string replacements in files",
    "write": "Writes a file to the local filesystem",
    "glob": "Fast file pattern matching tool",
    "grep": "Fast content search tool using regular expressions",
    "bash": "Executes a given bash command in a persistent shell session",
    "task": "Launch a subagent to handle complex, multistep tasks autonomously",
    "webfetch": "Fetches content from a specified URL",
    "websearch": "Search the web for information",
    "todowrite": "Create and maintain a structured task list for the current coding session",
    "question": "Use this tool to ask the user questions during execution",
    "skill": "Load a specialized skill when the task matches",
    "lsp": "Language Server Protocol tools (diagnostics, hover, references, etc.)",
    "list_mcp_resources": "Lists resources provided by connected MCP servers",
    "list_mcp_resource_templates": "Lists resource templates provided by connected MCP servers",
    "read_mcp_resource": "Read a specific resource from an MCP server",
}

BUILTIN_VERSION = "1.18"  # opencode version this list was verified against

# Tools grouped for display
TOOL_GROUPS = {
    "builtin": list(BUILTIN_TOOLS.keys()),
    "browser": [
        "browser_start", "browser_stop", "browser_open", "browser_navigate",
        "browser_back", "browser_snapshot", "browser_screenshot", "browser_click",
        "browser_type", "browser_evaluate", "browser_wait", "browser_close",
        "browser_console_logs", "browser_network_requests", "browser_current_url",
        "browser_storage_get", "browser_storage_set", "browser_clear_logs",
    ],
}

# Plugin tools: discovered via runtime (browser_* from plugin)
# These are version-pinned to the current browser plugin version
PLUGIN_TOOL_VERSION = "1.0.1"


# ---------------------------------------------------------------------------
# Local MCP tools (derived from config)
# ---------------------------------------------------------------------------

def discover_local_mcp_tools(mcp_config: dict) -> dict[str, str]:
    """Discover tools from local (stdio) MCP servers configured in mcp section.

    Only servers with type='local' AND not handled by the combiner are local.
    Returns {tool_prefix_base: server_name} for categorization.
    """
    local_servers: dict[str, str] = {}
    if not isinstance(mcp_config, dict):
        return local_servers
    for server_name, server_cfg in mcp_config.items():
        if not isinstance(server_cfg, dict):
            continue
        if server_cfg.get("type") != "local":
            continue
        local_servers[server_name] = server_name
    return local_servers


# ---------------------------------------------------------------------------
# Combiner (remote MCP) tool discovery via HTTP
# ---------------------------------------------------------------------------

@dataclass
class ToolCatalog:
    """Discovered tools organized by source."""
    builtin: dict[str, str] = field(default_factory=dict)
    combiner: dict[str, str] = field(default_factory=dict)   # mcp-combiner__*
    local: dict[str, str] = field(default_factory=dict)       # codegraph_*, memory_*
    browser: dict[str, str] = field(default_factory=dict)     # browser_*
    combiner_fetched: bool = False
    combiner_error: str | None = None
    builtin_version: str = BUILTIN_VERSION


_combiner_cache: dict[str, Any] | None = None
_combiner_cache_ts: float = 0.0
CACHE_TTL = 300  # 5 minutes


def fetch_combiner_tools(
    combiner_url: str = "http://127.0.0.1:9741/mcp",
    force_refresh: bool = False,
) -> dict[str, str]:
    """Fetch tool list from mcp-combiner via MCP Streamable HTTP protocol.

    Caches results for CACHE_TTL seconds. Returns {tool_name: description} dict.
    """
    global _combiner_cache, _combiner_cache_ts

    now = time.time()
    if not force_refresh and _combiner_cache is not None and (now - _combiner_cache_ts) < CACHE_TTL:
        return dict(_combiner_cache)

    try:
        # 1. Initialize session
        init_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "opencode-agent-tui", "version": "0.1.0"},
            },
        }
        req = urllib.request.Request(
            combiner_url,
            data=json.dumps(init_payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            session_id = resp.headers.get("Mcp-Session-Id", "")

        # 2. Extract session ID from SSE response
        if not session_id:
            match = re.search(r"mcp-session-id:\s*(\S+)", body)
            if match:
                session_id = match.group(1)

        if not session_id:
            return {}

        # 3. tools/list
        tools_payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        req2 = urllib.request.Request(
            combiner_url,
            data=json.dumps(tools_payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Mcp-Session-Id": session_id,
            },
            method="POST",
        )
        with urllib.request.urlopen(req2, timeout=10) as resp2:
            raw = resp2.read().decode()

        # 4. Parse SSE events
        tools: dict[str, str] = {}
        for line in raw.split("\n"):
            if line.startswith("data:"):
                event_data = line[len("data:"):].strip()
                if not event_data:
                    continue
                try:
                    msg = json.loads(event_data)
                    if "result" in msg and "tools" in msg["result"]:
                        for t in msg["result"]["tools"]:
                            name = t.get("name", "")
                            desc = t.get("description", "")
                            if name and not name.startswith("combiner__"):
                                tools[name] = desc
                except json.JSONDecodeError:
                    continue

        _combiner_cache = tools
        _combiner_cache_ts = now
        return tools

    except Exception as e:
        # Return cached data with error flag
        error_msg = str(e)
        if _combiner_cache is not None:
            return dict(_combiner_cache)
        return {}


def build_tool_catalog(
    mcp_config: dict,
    combiner_url: str = "http://127.0.0.1:9741/mcp",
    force_refresh: bool = False,
    opencode_version: str | None = None,
) -> ToolCatalog:
    """Build the complete tool catalog: built-in + combiner + local + browser."""
    catalog = ToolCatalog()
    catalog.builtin = dict(BUILTIN_TOOLS)

    # Combiner tools
    combiner_tools = fetch_combiner_tools(combiner_url, force_refresh)
    if combiner_tools:
        catalog.combiner = combiner_tools
        catalog.combiner_fetched = True
    else:
        catalog.combiner_error = (
            "Combiner unreachable. Check: systemctl --user status mcp-combiner"
        )

    # Local MCP tools
    local_servers = discover_local_mcp_tools(mcp_config)
    for server_name in local_servers:
        prefix = f"{server_name}_"
        catalog.local[prefix] = f"Tools from local MCP server: {server_name}"

    # Browser plugin tools
    catalog.browser = {t: "" for t in TOOL_GROUPS["browser"]}

    # Version check
    if opencode_version and opencode_version != BUILTIN_VERSION:
        # Warn if built-in tool list might be stale
        pass

    return catalog


def get_tool_display_name(raw_name: str) -> str:
    """Convert a raw tool name to a display-friendly format.

    mcp-combiner__github_search_code → github: search_code
    codegraph_where → codegraph: where
    """
    # Combiner tools: mcp-combiner__<backend>_<tool>
    if raw_name.startswith("mcp-combiner__"):
        rest = raw_name[len("mcp-combiner__"):]
        parts = rest.split("_", 1)
        if len(parts) == 2:
            return f"{parts[0]}: {parts[1]}"
        return f"combiner: {rest}"
    return raw_name
