"""Business logic for the MCP manager API.

Thin by design: TUKI only calls these functions from the API router today, but
the router/logic split matches the rest of the codebase (../logic/tasks.py) and
keeps the validation rules (unique name, secret masking) in one place.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import McpServer
from ..schemas import McpServerCreate, McpServerInfo, McpServerUpdate
from ..ai.tools import mcp_tools

# Sentinel returned by GET for http header values that are secrets
# (e.g. Authorization). The UI sends it back unchanged on update and the
# logic keeps the stored value. Never send the real secret back to the browser.
MASK = "\u2022\u2022\u2022\u2022\u2022\u2022"  # ••••••


def _masked_info(row: McpServer) -> McpServerInfo:
    """McpServerInfo with http header values replaced by MASK."""
    info = McpServerInfo.from_row(row)
    server = info.server
    if server.transport == "http" and server.headers:
        server = server.model_copy(update={"headers": {key: MASK for key in server.headers}})
        info = info.model_copy(update={"server": server})
    return info


def _check_unique_name(db: Session, name: str, exclude_id: int | None = None) -> None:
    query = db.query(McpServer).filter(McpServer.name == name)
    if exclude_id is not None:
        query = query.filter(McpServer.id != exclude_id)
    if query.first() is not None:
        raise HTTPException(status_code=400, detail=f"MCP server '{name}' already exists")


def list_mcp_servers_logic(db: Session) -> list[McpServerInfo]:
    rows = db.query(McpServer).order_by(McpServer.name).all()
    return [_masked_info(row) for row in rows]


def get_mcp_server_logic(db: Session, server_id: int) -> McpServerInfo:
    row = db.get(McpServer, server_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"MCP server {server_id} not found")
    return _masked_info(row)


def create_mcp_server_logic(db: Session, data: McpServerCreate) -> McpServerInfo:
    _check_unique_name(db, data.name)
    row = McpServer(
        name=data.name,
        transport=data.server.transport,
        config=data.server.model_dump(exclude={"transport"}),
        enabled=data.enabled,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _masked_info(row)


def update_mcp_server_logic(db: Session, server_id: int, data: McpServerUpdate) -> McpServerInfo:
    row = db.get(McpServer, server_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"MCP server {server_id} not found")
    if data.name is not None and data.name != row.name:
        _check_unique_name(db, data.name, exclude_id=row.id)
        row.name = data.name
    if data.enabled is not None:
        row.enabled = data.enabled
    if data.server is not None:
        config = data.server.model_dump(exclude={"transport"})
        # Masked-keep: the UI cannot see real header values, so a MASK value
        # means "leave the stored secret as-is". Only applies when both old and
        # new transports are http; switching transport starts with real values.
        if data.server.transport == "http" and row.transport == "http":
            old_headers = dict(row.config).get("headers") or {}
            headers = {}
            for key, value in (data.server.headers or {}).items():
                headers[key] = old_headers.get(key, "") if value == MASK else value
            config["headers"] = headers
        row.transport = data.server.transport
        row.config = config
    db.commit()
    db.refresh(row)
    return _masked_info(row)


def delete_mcp_server_logic(db: Session, server_id: int) -> dict:
    row = db.get(McpServer, server_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"MCP server {server_id} not found")
    name = row.name
    db.delete(row)
    db.commit()
    return {"deleted": name}


async def test_mcp_server_logic(db: Session, server_id: int) -> dict:
    """Open a live session against the server and list the tools it exposes."""
    row = db.get(McpServer, server_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"MCP server {server_id} not found")
    info = McpServerInfo.from_row(row)
    try:
        tools = await mcp_tools._fetch_mcp_tools(info)
        return {"ok": True, "tools": sorted(tool["function"]["name"] for tool in tools)}
    except Exception as exc:  # noqa: BLE001 - surface any transport error to the UI
        return {"ok": False, "error": mcp_tools._format_exception(exc)}