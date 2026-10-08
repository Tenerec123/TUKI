"""Pytest bootstrap: stub MCP tool discovery before any test module imports it.

Importing the real ``backend.ai.tools.discovery`` module runs discovery at
import time (DB query plus stdio/HTTP MCP connections and external processes),
so pytest collection must never execute it. Tests that need the names only
read the stub's empty schemas.
"""

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _discovery_unavailable(*_args, **_kwargs):
    raise RuntimeError("MCP discovery is stubbed out in tests")


def _seed_discovery_stub() -> None:
    if "backend.ai.tools.discovery" in sys.modules:
        return
    stub = types.ModuleType("backend.ai.tools.discovery")
    stub.ALL_TOOL_SCHEMAS = []
    stub.ORCHESTRATOR_TOOL_SCHEMAS = []
    stub.execute_tool_call = _discovery_unavailable
    stub.refresh_mcp_tools = _discovery_unavailable
    sys.modules["backend.ai.tools.discovery"] = stub


_seed_discovery_stub()
