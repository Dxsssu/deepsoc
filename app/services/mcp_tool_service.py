import logging
from typing import Any, Dict, Optional
from app.utils.mcp_servers.registry import execute_tool as execute_registered_tool
from app.utils.mcp_servers.registry import get_tool_catalog

logger = logging.getLogger(__name__)


class MCPToolService:
    """Execute dynamically registered local MCP tools."""

    def execute_tool(self, server_name: str, tool_name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = params or {}
        logger.info(f"调用本地MCP工具: server={server_name}, tool={tool_name}")
        return execute_registered_tool(server_name=server_name, tool_name=tool_name, params=params)

    def get_tool_catalog(self) -> Dict[str, Any]:
        return get_tool_catalog()
