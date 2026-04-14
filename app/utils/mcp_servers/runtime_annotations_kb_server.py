import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

mcp = FastMCP("runtime_annotations_kb_mcp")


def _resolve_sqlite_db_path() -> str:
    env_path = os.getenv("KNOWLEDGE_SQLITE_DB_PATH", "").strip()
    if env_path:
        return env_path
    project_root = Path(__file__).resolve().parents[3]
    return str(project_root / "knowledge_sqlite.db")


def _fetch_annotation_by_key(annotation_key: str) -> Optional[Dict[str, Any]]:
    db_path = _resolve_sqlite_db_path()
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        row = cursor.execute(
            """
            SELECT id, annotation_key, title, content_md, created_at, updated_at
            FROM runtime_annotations
            WHERE annotation_key = ?
            LIMIT 1
            """,
            (str(annotation_key or "").strip(),),
        ).fetchone()
        if not row:
            return None

        return {
            "id": int(row["id"]),
            "annotation_key": str(row["annotation_key"] or ""),
            "title": str(row["title"] or ""),
            "content_md": str(row["content_md"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }
    finally:
        conn.close()


@mcp.tool(name="runtime_annotation_lookup", description="按 annotation_key 精确查询实时标注")
def runtime_annotation_lookup(annotation_key: str) -> Dict[str, Any]:
    record = _fetch_annotation_by_key(annotation_key)
    if not record:
        return {
            "found": False,
            "annotation_key": str(annotation_key or "").strip(),
            "message": "未找到实时标注记录",
        }
    return {"found": True, "annotation": record}


@mcp.tool(name="runtime_annotation_search", description="按关键词检索实时标注（匹配key/title/content）")
def runtime_annotation_search(keyword: str, limit: int = 10) -> Dict[str, Any]:
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
            SELECT id, annotation_key, title, content_md, created_at, updated_at
            FROM runtime_annotations
            WHERE annotation_key LIKE ?
               OR title LIKE ?
               OR content_md LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (pattern, pattern, pattern, safe_limit),
        ).fetchall()

        items: List[Dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "id": int(row["id"]),
                    "annotation_key": str(row["annotation_key"] or ""),
                    "title": str(row["title"] or ""),
                    "content_md": str(row["content_md"] or ""),
                    "created_at": str(row["created_at"] or ""),
                    "updated_at": str(row["updated_at"] or ""),
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

