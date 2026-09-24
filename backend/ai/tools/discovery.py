"""Auto-discovery orchestrator: combines frozen LOCAL tools with live MCP tools."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from .mcp_tools import _discover_mcp_tools, _load_mcps, _discover_mcp_tools_with_status, execute_mcp_tool
from .local_tools import ToolDict, TOOL_SCHEMAS, execute_local_tool


def _run_sync(coro):
    """Run a coroutine to completion even if an event loop is already running.

    asyncio.run() refuses when a loop is active (uvicorn imports the app INSIDE
    its running serve() loop), and on Python 3.13 a private loop's
    run_until_complete() is rejected too. Safest path: run asyncio.run() in a
    worker thread, where no loop is running at all.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _discover_tools():
    """Build the combined schema list at import time."""
    mcp_schemas = _run_sync(_discover_mcp_tools())
    return [*TOOL_SCHEMAS, *mcp_schemas]


# ── Run discovery once at import time ──────────────────────────────────

ALL_TOOL_SCHEMAS = _discover_tools()

ORCHESTRATOR_BLACKLIST = {
    "WebFetch",
}

ORCHESTRATOR_TOOL_SCHEMAS = [
    s for s in ALL_TOOL_SCHEMAS
    if s['function']['name'] not in ORCHESTRATOR_BLACKLIST
]


async def refresh_mcp_tools() -> list[dict]:
    """Re-discover MCP servers from the DB and splice their schemas in place.

    Only the MCP half is refreshed — local tool schemas never change at
    runtime. Call after MCP CRUD mutations. Returns per-server diagnostics so
    the UI can report failures:
      [{"name": ..., "status": "ok", "tools": [...]} |
       {"name": ..., "status": "error", "error": "..."}]
    """
    mcps = _load_mcps()
    new_schemas, diagnostics = await _discover_mcp_tools_with_status(mcps)
    ALL_TOOL_SCHEMAS[:] = [*TOOL_SCHEMAS, *new_schemas]
    ORCHESTRATOR_TOOL_SCHEMAS[:] = [
        s for s in ALL_TOOL_SCHEMAS
        if s['function']['name'] not in ORCHESTRATOR_BLACKLIST
    ]
    return diagnostics


# ── Dispatch ───────────────────────────────────────────────────────────

async def execute_tool_call(id: int, name: str, arguments: str) -> tuple:
    """Execute a tool by name with JSON arguments string. Returns (id, name, result).
    Supports inline (fast pure), async, sync-to-thread, and MCP tools.
    """
    func = ToolDict.get(name)
    try:
        if func:
            return await execute_local_tool(id, name, func, arguments)
        elif (parts := name.split("_", 2)) and parts[0] == "mcp":
            return await execute_mcp_tool(id, parts[1], parts[2], arguments)
        else:
            print(f"[TOOL] {name} NOT FOUND in ToolDict")
            return id, name, f'Error: Tool {name} not found'

    except Exception as e:
        print(f"[TOOL] {name} → ERROR: {e}")
        return id, name, f"Execution Error: {str(e)}"