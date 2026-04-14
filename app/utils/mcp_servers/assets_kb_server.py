import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

mcp = FastMCP("assets_kb_mcp")


def _resolve_sqlite_db_path() -> str:
    env_path = os.getenv("KNOWLEDGE_SQLITE_DB_PATH", "").strip()
    if env_path:
        return env_path
    project_root = Path(__file__).resolve().parents[3]
    return str(project_root / "knowledge_sqlite.db")


def _parse_metadata(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _fetch_asset_by_key(asset_key: str) -> Optional[Dict[str, Any]]:
    db_path = _resolve_sqlite_db_path()
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        row = cursor.execute(
            """
            SELECT id, asset_key, asset_type, asset_group, criticality, owner, metadata_json, created_at, updated_at
            FROM assets
            WHERE asset_key = ?
            LIMIT 1
            """,
            (str(asset_key or "").strip(),),
        ).fetchone()
        if not row:
            return None

        return {
            "id": int(row["id"]),
            "asset_key": str(row["asset_key"] or ""),
            "asset_type": str(row["asset_type"] or ""),
            "asset_group": str(row["asset_group"] or ""),
            "criticality": str(row["criticality"] or ""),
            "owner": str(row["owner"] or ""),
            "metadata_json": _parse_metadata(row["metadata_json"]),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }
    finally:
        conn.close()


@mcp.tool(name="asset_lookup", description="按 asset_key 精确查询资产信息")
def asset_lookup(asset_key: str) -> Dict[str, Any]:
    record = _fetch_asset_by_key(asset_key)
    if not record:
        return {
            "found": False,
            "asset_key": str(asset_key or "").strip(),
            "message": "未找到资产记录",
        }
    return {"found": True, "asset": record}


@mcp.tool(name="asset_search", description="按关键词检索资产（匹配key/分组/责任人/扩展字段）")
def asset_search(keyword: str, limit: int = 10) -> Dict[str, Any]:
    db_path = _resolve_sqlite_db_path()
    if not os.path.exists(db_path):
        return {"found": False, "items": [], "message": f"知识库不存在: {db_path}"}

    safe_limit = max(1, min(int(limit), 100))
    pattern = f"%{str(keyword or '').strip()}%"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        rows = cursor.execute(
            """
            SELECT id, asset_key, asset_type, asset_group, criticality, owner, metadata_json
            FROM assets
            WHERE asset_key LIKE ?
               OR asset_group LIKE ?
               OR owner LIKE ?
               OR metadata_json LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, safe_limit),
        ).fetchall()

        items: List[Dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "id": int(row["id"]),
                    "asset_key": str(row["asset_key"] or ""),
                    "asset_type": str(row["asset_type"] or ""),
                    "asset_group": str(row["asset_group"] or ""),
                    "criticality": str(row["criticality"] or ""),
                    "owner": str(row["owner"] or ""),
                    "metadata_json": _parse_metadata(row["metadata_json"]),
                }
            )

        return {
            "found": len(items) > 0,
            "keyword": str(keyword or "").strip(),
            "count": len(items),
            "items": items,
        }
    finally:
        conn.close()

