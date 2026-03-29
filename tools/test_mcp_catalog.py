#!/usr/bin/env python3
"""Smoke test: discover currently registered MCP servers and tools."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.utils.mcp_servers.registry import get_tool_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test reading current MCP servers and MCP tools."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output full catalog as JSON.",
    )
    return parser


def _safe_list(value: Any) -> List[Dict[str, Any]]:
    return value if isinstance(value, list) else []


def validate_catalog(catalog: Dict[str, Any]) -> tuple[bool, str]:
    servers = _safe_list(catalog.get("mcp_servers"))
    if not servers:
        return False, "未发现任何MCP server"

    tool_count = 0
    for server in servers:
        tools = _safe_list(server.get("tools"))
        tool_count += len(tools)
    if tool_count == 0:
        return False, "已发现MCP server，但没有任何MCP tool"

    return True, f"发现 {len(servers)} 个MCP server，{tool_count} 个MCP tool"


def print_human_readable(catalog: Dict[str, Any]) -> None:
    servers = _safe_list(catalog.get("mcp_servers"))
    print("MCP Discovery Result:")
    for server in servers:
        server_name = server.get("name", "")
        tools = _safe_list(server.get("tools"))
        print(f"- server: {server_name} ({len(tools)} tools)")
        for tool in tools:
            tool_name = tool.get("name", "")
            desc = tool.get("desc", "")
            print(f"  - tool: {tool_name} | desc: {desc}")


def main() -> int:
    args = build_parser().parse_args()
    catalog = get_tool_catalog()
    ok, message = validate_catalog(catalog)

    if args.json:
        print(json.dumps(catalog, ensure_ascii=False, indent=2))
    else:
        print_human_readable(catalog)

    print(f"\nResult: {message}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

