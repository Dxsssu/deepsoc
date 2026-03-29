import asyncio
import importlib
import json
import logging
import pkgutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_SERVER_REGISTRY: Dict[str, FastMCP] = {}
_SERVERS_LOADED = False


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result_holder: Dict[str, Any] = {}
    error_holder: Dict[str, Exception] = {}

    def _runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result_holder["value"] = loop.run_until_complete(coro)
        except Exception as exc:
            error_holder["error"] = exc
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "error" in error_holder:
        raise error_holder["error"]
    return result_holder.get("value")


def _discover_fastmcp_instances(module: Any) -> List[FastMCP]:
    instances: List[FastMCP] = []
    default_mcp = getattr(module, "mcp", None)
    if isinstance(default_mcp, FastMCP):
        instances.append(default_mcp)

    for _, value in vars(module).items():
        if isinstance(value, FastMCP) and value not in instances:
            instances.append(value)
    return instances


def _auto_load_servers() -> None:
    global _SERVERS_LOADED
    if _SERVERS_LOADED:
        return

    current_dir = Path(__file__).resolve().parent
    package_name = "app.utils.mcp_servers"

    for module in pkgutil.iter_modules([str(current_dir)]):
        if module.name.startswith("_") or module.name in {"registry"}:
            continue
        if not module.name.endswith("_server"):
            continue

        imported_module = importlib.import_module(f"{package_name}.{module.name}")
        for mcp in _discover_fastmcp_instances(imported_module):
            server_name = str(getattr(mcp, "name", "")).strip()
            if not server_name:
                continue
            if server_name in _SERVER_REGISTRY:
                logger.warning(f"MCP服务名称重复，后者将覆盖前者: {server_name}")
            _SERVER_REGISTRY[server_name] = mcp
            logger.info(f"加载MCP服务: {server_name} ({module.name})")

    _SERVERS_LOADED = True


def _extract_text_content(content_items: Any) -> Any:
    if not isinstance(content_items, list):
        return content_items

    texts: List[str] = []
    for item in content_items:
        text = getattr(item, "text", None)
        if text is not None:
            texts.append(str(text))
        else:
            texts.append(str(item))

    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except Exception:
            return texts[0]
    return texts


def _normalize_tool_result(tool_result: Any) -> Any:
    structured_content = getattr(tool_result, "structured_content", None)
    if structured_content is not None:
        return structured_content

    content = getattr(tool_result, "content", None)
    if content is not None:
        return _extract_text_content(content)

    return str(tool_result)


def _schema_to_params(schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required_set = set(schema.get("required", []) if isinstance(schema, dict) else [])
    params: List[Dict[str, Any]] = []

    if not isinstance(properties, dict):
        return params

    for param_name, spec in properties.items():
        if not isinstance(spec, dict):
            spec = {}
        item: Dict[str, Any] = {
            "name": str(param_name),
            "desc": str(spec.get("description", "")),
            "required": param_name in required_set,
        }
        if "type" in spec:
            item["type"] = spec.get("type")
        if "default" in spec:
            item["default"] = spec.get("default")
        params.append(item)
    return params


def execute_tool(server_name: str, tool_name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _auto_load_servers()
    server = _SERVER_REGISTRY.get(str(server_name))
    if not server:
        return {"status": "failed", "message": f"未找到MCP服务: {server_name}"}

    safe_params = params if isinstance(params, dict) else {}
    try:
        tool_result = _run_async(server.call_tool(str(tool_name), safe_params))
        response = _normalize_tool_result(tool_result)
        return {
            "status": "success",
            "message": "MCP工具执行成功",
            "data": {
                "server": server_name,
                "tool": tool_name,
                "response": response,
            },
        }
    except Exception as exc:
        logger.exception(f"MCP工具执行失败: server={server_name}, tool={tool_name}")
        return {
            "status": "failed",
            "message": f"MCP工具执行失败: {exc}",
            "data": {"server": server_name, "tool": tool_name},
        }


def get_tool_catalog() -> Dict[str, Any]:
    _auto_load_servers()
    server_items: List[Dict[str, Any]] = []

    for server_name in sorted(_SERVER_REGISTRY.keys()):
        server = _SERVER_REGISTRY[server_name]
        tools = _run_async(server.list_tools())
        tool_items: List[Dict[str, Any]] = []

        for tool in sorted(tools, key=lambda x: str(getattr(x, "name", ""))):
            schema = getattr(tool, "parameters", {}) or {}
            tool_items.append(
                {
                    "name": str(getattr(tool, "name", "")),
                    "desc": str(getattr(tool, "description", "") or ""),
                    "params": _schema_to_params(schema),
                }
            )

        server_items.append({"name": server_name, "tools": tool_items})

    return {"mcp_servers": server_items}


def render_tool_catalog_yaml() -> str:
    catalog = get_tool_catalog()
    return "```yaml\n" + yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False) + "```"

