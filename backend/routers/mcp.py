from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import McpServerCreate, McpServerInfo, McpServerUpdate
from ..ai.tools.discovery import refresh_mcp_tools
from ..logic.mcp import (
    create_mcp_server_logic,
    delete_mcp_server_logic,
    get_mcp_server_logic,
    list_mcp_servers_logic,
    test_mcp_server_logic,
    update_mcp_server_logic,
)

router = APIRouter(
    prefix="/api/mcp",
    tags=["mcp"],
)


@router.get("/", response_model=list[McpServerInfo])
def list_servers(db: Session = Depends(get_db)):
    """All configured MCP servers (http header values masked)."""
    return list_mcp_servers_logic(db=db)


@router.get("/{server_id}", response_model=McpServerInfo)
def get_server(server_id: int, db: Session = Depends(get_db)):
    """One MCP server (http header values masked)."""
    return get_mcp_server_logic(db=db, server_id=server_id)


@router.post("/", status_code=201)
async def create_server(payload: McpServerCreate, db: Session = Depends(get_db)):
    """Create a server, then refresh the runtime tool registry."""
    server = create_mcp_server_logic(db=db, data=payload)
    refresh = await refresh_mcp_tools()
    return {"server": server, "refresh": refresh}


@router.put("/{server_id}")
async def update_server(server_id: int, payload: McpServerUpdate, db: Session = Depends(get_db)):
    """Update a server, then refresh the runtime tool registry."""
    server = update_mcp_server_logic(db=db, server_id=server_id, data=payload)
    refresh = await refresh_mcp_tools()
    return {"server": server, "refresh": refresh}


@router.delete("/{server_id}")
async def delete_server(server_id: int, db: Session = Depends(get_db)):
    """Delete a server, then refresh the runtime tool registry."""
    result = delete_mcp_server_logic(db=db, server_id=server_id)
    refresh = await refresh_mcp_tools()
    return {**result, "refresh": refresh}


@router.post("/{server_id}/test")
async def test_server(server_id: int, db: Session = Depends(get_db)):
    """Open a live session and list the tools the server exposes."""
    return await test_mcp_server_logic(db=db, server_id=server_id)