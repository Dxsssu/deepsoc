#!/usr/bin/env python3
"""Initialize SOP knowledge base in Qdrant."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.knowledge_base_service import DEFAULT_SOP_SEED_DOCUMENTS, KnowledgeBaseService


DEFAULT_SEED_FILE = ROOT_DIR / "sql_data" / "knowledge" / "sop_seed.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="初始化SOP知识库到Qdrant。")
    parser.add_argument(
        "--seed-file",
        type=str,
        default=str(DEFAULT_SEED_FILE),
        help=f"SOP种子文件路径（默认: {DEFAULT_SEED_FILE}）",
    )
    parser.add_argument(
        "--collection",
        type=str,
        default="",
        help="覆盖默认SOP collection名称（可选）。",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="重建collection（会清空原有数据）。",
    )
    parser.add_argument(
        "--query",
        type=str,
        default="",
        help="初始化后执行一次语义检索验证（可选）。",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="检索返回条数，默认3。",
    )
    return parser


def load_seed_documents(seed_file: str) -> List[Dict[str, Any]]:
    file_path = Path(seed_file)
    if file_path.exists():
        content = file_path.read_text(encoding="utf-8-sig")
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError(f"种子文件格式错误，期望list，实际为: {type(data)}")
        return data

    print(f"[WARN] 种子文件不存在，使用内置默认SOP种子: {file_path}")
    return DEFAULT_SOP_SEED_DOCUMENTS


def main() -> int:
    args = build_parser().parse_args()
    service = KnowledgeBaseService()
    collection_name = args.collection.strip() or service.sop_collection

    docs = load_seed_documents(args.seed_file)
    result = service.upsert_sop_knowledge(
        documents=docs,
        collection_name=collection_name,
        recreate_collection=args.recreate,
    )

    print("=== SOP Knowledge Base Init Result ===")
    print(f"qdrant_url: {service.qdrant_url}")
    print(f"collection: {result['collection']}")
    print(f"embedding_provider: {service.embedding_provider}")
    print(f"upserted: {result['upserted']}")

    if args.query.strip():
        print("\n=== Retrieval Smoke Test ===")
        retrieval = service.search_sop(
            query_text=args.query.strip(),
            limit=max(args.top_k, 1),
            collection_name=collection_name,
        )
        print(json.dumps(retrieval, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
