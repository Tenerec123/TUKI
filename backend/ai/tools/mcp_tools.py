"""MCP tools: discovery + execution for user-configured MCP servers."""

import asyncio
import json
from contextlib import asynccontextmanager
import httpx
from ...database import SessionLocal
from ...models import McpServer
from ...schemas import McpServerInfo


# Live MCP server configs keyed by name. Updated at import and on every refresh.
MCP_SERVERS: dict[str, McpServerInfo] = {}


def _load_mcps():
    with SessionLocal() as db:
        servers = db.query(McpServer).where(McpServer.enabled == True)
        return [McpServerInfo.from_row(r) for r in servers]


def format_mcp_tool(tool, mcp_name):
    """Convert an MCP Tool object into an OpenAI SDK function tool schema."""
    parameters = dict(tool.inputSchema or {})
    parameters.pop("$schema", None)  # JSON Schema version key — not needed by OpenAI
    parameters.setdefault("type", "object")
    parameters.setdefault("properties", {})
    return {
        "type": "function",
        "function": {
            "name": f'mcp_{mcp_name}_{tool.name}',
            "description": tool.description or "",
            "parameters": parameters,
        },
    }


@asynccontextmanager
async def open_mcp_session(mcp: McpServerInfo):
    """Open a ClientSession for an MCP server (stdio or http) and yield it."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    if mcp.server.transport == "stdio":
        server_params = StdioServerParameters(command=mcp.server.command, args=mcp.server.args)
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:
        # 30s timeout: serverless hosts (Vercel, etc.) cold-start in seconds and
        # httpx's 5s default is too tight for real remote servers.
        async with httpx.AsyncClient(headers=mcp.server.headers or {}, timeout=30) as http_client:
            async with streamable_http_client(mcp.server.url, http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session


def _format_exception(exc: BaseException) -> str:
    """Flatten anyio/asyncio ExceptionGroups into the first real leaf message.

    MCP SDK internals wrap spawn/transport failures in an anyio TaskGroup, so
    str(exc) gives 'unhandled errors in a TaskGroup (1 sub-exception)' — useless
    for the UI. Unwrap to the actual cause instead.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return str(exc)


async def _fetch_mcp_tools(mcp: McpServerInfo):
    async with open_mcp_session(mcp) as session:
        result = await session.list_tools()
        return [format_mcp_tool(tool, mcp.name) for tool in result.tools]


async def _discover_mcp_tools_with_status(mcps):
    """Discover tools per server, capturing failures.

    Returns (schemas, diagnostics) where diagnostics is a list of:
      {"name": ..., "status": "ok", "tools": [...]}
      {"name": ..., "status": "error", "error": "..."}
    Only successful servers contribute schemas — a broken server must not
    expose tools that would always fail at call time.
    """
    MCP_SERVERS.update({m.name: m for m in mcps})
    results = await asyncio.gather(*[_fetch_mcp_tools(mcp) for mcp in mcps], return_exceptions=True)
    schemas = []
    diagnostics = []
    for mcp, res in zip(mcps, results):
        if isinstance(res, BaseException):
            diagnostics.append({"name": mcp.name, "status": "error", "error": _format_exception(res)})
        else:
            schemas.extend(res)
            diagnostics.append({
                "name": mcp.name,
                "status": "ok",
                "tools": [s["function"]["name"] for s in res],
            })
    return schemas, diagnostics


async def _discover_mcp_tools(mcps=None):
    """Discover tool schemas for all enabled servers. Used at import time."""
    if mcps is None:
        mcps = _load_mcps()
    schemas, _ = await _discover_mcp_tools_with_status(mcps)
    return schemas


def _format_mcp_result(result) -> str:
    """Convert an MCP CallToolResult into a plain string for the model."""
    texts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    text = "\n".join(texts)
    if result.isError:
        return f"MCP tool error: {text}" if text else f"MCP tool error: {result}"
    if text:
        return text
    structured = getattr(result, "structuredContent", None)
    return json.dumps(structured or {}, default=str)


async def execute_mcp_tool(id: int, provider: str, tool: str, arguments: str) -> tuple:
    """Execute an MCP tool against its server.

    The session opens and closes INSIDE this call, in the same task — see
    open_mcp_session for the anyio cancel-scope constraint.
    """
    full_name = f"mcp_{provider}_{tool}"
    try:
        server = MCP_SERVERS[provider]
    except KeyError:
        return id, full_name, f"Error: MCP server '{provider}' not found or disabled"
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        print(f"[TOOL] {full_name}(args={args})")
        async with open_mcp_session(server) as session:
            result = await session.call_tool(tool, args)
        result_str = _format_mcp_result(result)
        print(f"[TOOL] {full_name} → OK ({len(result_str)} chars)")
        return id, full_name, result_str
    except Exception as e:
        print(f"[TOOL] {full_name} → ERROR: {e}")
        return id, full_name, f"MCP Execution Error: {str(e)}"