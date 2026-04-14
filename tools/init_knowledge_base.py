#!/usr/bin/env python3
"""Unified knowledge base initializer for SQLite and Qdrant."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from qdrant_client.models import PointStruct


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DEFAULT_SEED_ROOT = ROOT_DIR / "sql_data" / "knowledge"

# SQLite seeds
DEFAULT_KB_DB_PATH = ROOT_DIR / "knowledge_sqlite.db"
DEFAULT_SOP_FILE = DEFAULT_SEED_ROOT / "sop" / "sop_seed.json"
DEFAULT_ASSETS_FILE = DEFAULT_SEED_ROOT / "assets" / "assets_seed.json"
DEFAULT_ANNOTATIONS_FILE = DEFAULT_SEED_ROOT / "runtime_annotations" / "runtime_annotations_seed.json"

# Qdrant seeds
DEFAULT_CASES_SEED_FILE = DEFAULT_SEED_ROOT / "kb_cases" / "kb_cases_seed.json"
DEFAULT_SECURITY_SEED_FILE = DEFAULT_SEED_ROOT / "kb_security_knowledge" / "kb_security_knowledge_seed.json"

_EMBED_K1 = 1.5
_EMBED_B = 0.75
_EMBED_RANDOM_PROJECTIONS = 8
_TOKEN_WORD_RE = re.compile(r"[a-z0-9_]+")
_TOKEN_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="统一初始化知识库（SQLite + Qdrant）。")
    parser.add_argument(
        "--target",
        choices=["sqlite", "qdrant", "all"],
        default="all",
        help="初始化目标：sqlite / qdrant / all（默认 all）。",
    )

    # SQLite options
    parser.add_argument("--db-path", type=str, default=str(DEFAULT_KB_DB_PATH), help="SQLite 数据库路径。")
    parser.add_argument("--sop-file", type=str, default=str(DEFAULT_SOP_FILE), help="SOP seed 文件路径。")
    parser.add_argument("--assets-file", type=str, default=str(DEFAULT_ASSETS_FILE), help="资产 seed 文件路径。")
    parser.add_argument(
        "--annotations-file",
        type=str,
        default=str(DEFAULT_ANNOTATIONS_FILE),
        help="实时标注 seed 文件路径。",
    )
    parser.add_argument("--recreate-sqlite", action="store_true", help="重建 SQLite 三张表。")

    # Qdrant options
    parser.add_argument("--cases-file", type=str, default=str(DEFAULT_CASES_SEED_FILE), help="历史案例 seed 文件。")
    parser.add_argument(
        "--security-file",
        type=str,
        default=str(DEFAULT_SECURITY_SEED_FILE),
        help="安全知识 seed 文件。",
    )
    parser.add_argument("--cases-collection", type=str, default="", help="覆盖 kb_cases collection 名称。")
    parser.add_argument(
        "--security-collection",
        type=str,
        default="",
        help="覆盖 kb_security_knowledge collection 名称。",
    )
    parser.add_argument("--recreate-cases", action="store_true", help="重建 kb_cases collection。")
    parser.add_argument("--recreate-security", action="store_true", help="重建 kb_security_knowledge collection。")
    return parser


def load_json_list(file_path: str) -> List[Dict[str, Any]]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"seed 文件不存在: {path}")
    content = path.read_text(encoding="utf-8-sig")
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError(f"seed 文件格式错误，期望 list: {path}")
    return data


def create_sqlite_tables(conn: sqlite3.Connection, recreate: bool = False) -> None:
    cur = conn.cursor()
    if recreate:
        cur.execute("DROP TABLE IF EXISTS runtime_annotations")
        cur.execute("DROP TABLE IF EXISTS assets")
        cur.execute("DROP TABLE IF EXISTS sop")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sop (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type_key TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            content_md TEXT NOT NULL DEFAULT '',
            version TEXT NOT NULL DEFAULT '1.0.0',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_key TEXT NOT NULL UNIQUE,
            asset_type TEXT,
            asset_group TEXT,
            criticality TEXT,
            owner TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            annotation_key TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            content_md TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    conn.commit()


def normalize_sop_item(item: Dict[str, Any]) -> Dict[str, Any]:
    if item.get("alert_type_key"):
        return {
            "alert_type_key": str(item["alert_type_key"]).strip(),
            "title": str(item.get("title", "")).strip() or str(item["alert_type_key"]).strip(),
            "content_md": str(item.get("content_md", "")).strip(),
            "version": str(item.get("version", "1.0.0")).strip(),
            "is_active": 1 if bool(item.get("is_active", True)) else 0,
        }

    # 兼容旧字段格式
    sop_name = str(item.get("sop_name", "")).strip()
    sop_index = str(item.get("sop_index", "")).strip()
    workflow_steps = item.get("workflow_steps") if isinstance(item.get("workflow_steps"), list) else []
    if not sop_name or not workflow_steps:
        raise ValueError(f"SOP记录缺少必要字段: {item}")

    key_candidate = sop_index.split()[0].strip().lower().replace("/", "_")
    if not key_candidate:
        key_candidate = sop_name.lower().replace(" ", "_")

    lines: List[str] = []
    for step in workflow_steps:
        if not isinstance(step, dict):
            continue
        step_name = str(step.get("step_name", "")).strip()
        step_content = str(step.get("step_content", "")).strip()
        if step_name:
            lines.append(f"# {step_name}")
        if step_content:
            lines.append("")
            lines.append(step_content)
            lines.append("")

    return {
        "alert_type_key": key_candidate,
        "title": sop_name,
        "content_md": "\n".join(lines).strip(),
        "version": str(item.get("version", "1.0.0")).strip(),
        "is_active": 1 if bool(item.get("is_active", True)) else 0,
    }


def upsert_sop(conn: sqlite3.Connection, rows: List[Dict[str, Any]]) -> int:
    now_text = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()
    upserted = 0
    for item in rows:
        row = normalize_sop_item(item)
        if not row["alert_type_key"] or not row["title"]:
            raise ValueError(f"SOP记录缺少 key/title: {item}")
        cur.execute(
            """
            INSERT INTO sop (alert_type_key, title, content_md, version, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(alert_type_key) DO UPDATE SET
                title=excluded.title,
                content_md=excluded.content_md,
                version=excluded.version,
                is_active=excluded.is_active,
                updated_at=excluded.updated_at
            """,
            (
                row["alert_type_key"],
                row["title"],
                row["content_md"],
                row["version"],
                row["is_active"],
                now_text,
                now_text,
            ),
        )
        upserted += 1
    conn.commit()
    return upserted


def upsert_assets(conn: sqlite3.Connection, rows: List[Dict[str, Any]]) -> int:
    now_text = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()
    upserted = 0
    for row in rows:
        asset_key = str(row.get("asset_key", "")).strip()
        if not asset_key:
            raise ValueError(f"asset 记录缺少 asset_key: {row}")
        metadata_json = row.get("metadata_json", {})
        cur.execute(
            """
            INSERT INTO assets (
                asset_key, asset_type, asset_group, criticality, owner, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_key) DO UPDATE SET
                asset_type=excluded.asset_type,
                asset_group=excluded.asset_group,
                criticality=excluded.criticality,
                owner=excluded.owner,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                asset_key,
                str(row.get("asset_type", "")).strip() or None,
                str(row.get("asset_group", "")).strip() or None,
                str(row.get("criticality", "")).strip() or None,
                str(row.get("owner", "")).strip() or None,
                json.dumps(metadata_json if isinstance(metadata_json, dict) else {}, ensure_ascii=False),
                now_text,
                now_text,
            ),
        )
        upserted += 1
    conn.commit()
    return upserted


def upsert_runtime_annotations(conn: sqlite3.Connection, rows: List[Dict[str, Any]]) -> int:
    now_text = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()
    upserted = 0
    for row in rows:
        annotation_key = str(row.get("annotation_key", "")).strip()
        title = str(row.get("title", "")).strip()
        content_md = str(row.get("content_md", "")).strip()
        if not annotation_key or not title:
            raise ValueError(f"runtime annotation 缺少 annotation_key/title: {row}")
        cur.execute(
            """
            INSERT INTO runtime_annotations (annotation_key, title, content_md, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(annotation_key) DO UPDATE SET
                title=excluded.title,
                content_md=excluded.content_md,
                updated_at=excluded.updated_at
            """,
            (
                annotation_key,
                title,
                content_md,
                now_text,
                now_text,
            ),
        )
        upserted += 1
    conn.commit()
    return upserted


def count_table(conn: sqlite3.Connection, table_name: str) -> int:
    cur = conn.cursor()
    row = cur.execute(f"SELECT COUNT(1) FROM {table_name}").fetchone()
    return int(row[0]) if row else 0


def run_sqlite_init(args: argparse.Namespace) -> None:
    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    sop_rows = load_json_list(args.sop_file)
    asset_rows = load_json_list(args.assets_file)
    annotation_rows = load_json_list(args.annotations_file)

    conn = sqlite3.connect(str(db_path))
    try:
        create_sqlite_tables(conn, recreate=args.recreate_sqlite)
        sop_count = upsert_sop(conn, sop_rows)
        asset_count = upsert_assets(conn, asset_rows)
        annotation_count = upsert_runtime_annotations(conn, annotation_rows)

        print("=== SQLite Knowledge Base Init Result ===")
        print(f"db_path: {db_path}")
        print(f"sop_upserted: {sop_count}, total_in_table: {count_table(conn, 'sop')}")
        print(f"assets_upserted: {asset_count}, total_in_table: {count_table(conn, 'assets')}")
        print(
            "runtime_annotations_upserted: "
            f"{annotation_count}, total_in_table: {count_table(conn, 'runtime_annotations')}"
        )
    finally:
        conn.close()


def load_seed_documents(seed_file: str, fallback_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    path = Path(seed_file)
    if not path.exists():
        print(f"[WARN] 种子文件不存在，使用内置默认数据: {path}")
        return fallback_docs

    content = path.read_text(encoding="utf-8-sig")
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError(f"种子文件格式错误，期望 list，实际: {type(data)}")
    return data


def _serialize_text_fragment(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _tokenize_text(text: str) -> List[str]:
    content = (text or "").strip().lower()
    if not content:
        return ["empty"]

    tokens: List[str] = []

    words = _TOKEN_WORD_RE.findall(content)
    tokens.extend([f"w:{word}" for word in words if word])
    for idx in range(len(words) - 1):
        tokens.append(f"wb:{words[idx]}_{words[idx + 1]}")

    for chunk in _TOKEN_CJK_RE.findall(content):
        if not chunk:
            continue
        chars = [ch for ch in chunk if ch.strip()]
        tokens.extend([f"c1:{ch}" for ch in chars])
        if len(chars) >= 2:
            for idx in range(len(chars) - 1):
                tokens.append(f"c2:{chars[idx]}{chars[idx + 1]}")
        if len(chars) >= 3:
            for idx in range(len(chars) - 2):
                tokens.append(f"c3:{chars[idx]}{chars[idx + 1]}{chars[idx + 2]}")

    return tokens or ["empty"]


def _bm25_token_weights(corpus_tokens: List[List[str]]) -> List[Dict[str, float]]:
    if not corpus_tokens:
        return []

    doc_count = len(corpus_tokens)
    doc_lengths = [len(tokens) for tokens in corpus_tokens]
    avg_doc_len = (sum(doc_lengths) / doc_count) if doc_count > 0 else 1.0
    if avg_doc_len <= 0:
        avg_doc_len = 1.0

    doc_freq: Counter[str] = Counter()
    for tokens in corpus_tokens:
        doc_freq.update(set(tokens))

    weighted_docs: List[Dict[str, float]] = []
    for tokens in corpus_tokens:
        tf = Counter(tokens)
        length = max(1, len(tokens))
        token_weights: Dict[str, float] = {}
        for token, freq in tf.items():
            df = max(1, int(doc_freq.get(token, 1)))
            idf = math.log(1.0 + ((doc_count - df + 0.5) / (df + 0.5)))
            numerator = freq * (_EMBED_K1 + 1.0)
            denominator = freq + _EMBED_K1 * (1.0 - _EMBED_B + _EMBED_B * (length / avg_doc_len))
            weight = idf * (numerator / max(1e-12, denominator))
            token_weights[token] = float(weight)
        weighted_docs.append(token_weights)
    return weighted_docs


def _stable_projection_slots(token: str, vector_size: int, slots: int) -> Iterable[Tuple[int, float]]:
    token_seed = token.encode("utf-8")
    for i in range(slots):
        digest = hashlib.sha256(token_seed + b"#" + str(i).encode("ascii")).digest()
        index = int.from_bytes(digest[:4], byteorder="big") % vector_size
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        yield index, sign


def _weights_to_vector(token_weights: Dict[str, float], vector_size: int) -> List[float]:
    vector = [0.0] * vector_size
    for token, weight in token_weights.items():
        if not token:
            continue
        for index, sign in _stable_projection_slots(
            token=token,
            vector_size=vector_size,
            slots=_EMBED_RANDOM_PROJECTIONS,
        ):
            vector[index] += sign * weight

    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 1e-12:
        return vector
    return [value / norm for value in vector]


def _build_offline_vectors(texts: List[str], vector_size: int) -> List[List[float]]:
    tokens_corpus = [_tokenize_text(text) for text in texts]
    weighted_corpus = _bm25_token_weights(tokens_corpus)
    return [_weights_to_vector(token_weights=weights, vector_size=vector_size) for weights in weighted_corpus]


def _normalize_case_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    case_id = str(doc.get("id", "")).strip() or str(uuid.uuid4())
    alert_type = str(doc.get("alert_type", "")).strip()
    if not alert_type:
        raise ValueError("案例知识文档必须包含 alert_type")

    return {
        "id": case_id,
        "alert_type": alert_type,
        "severity": str(doc.get("severity", "")).strip() or "unknown",
        "alert_payload": doc.get("alert_payload", ""),
        "actions": doc.get("actions", ""),
        "closed_at": str(doc.get("closed_at", "")).strip(),
    }


def _normalize_security_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    item_id = str(doc.get("id", "")).strip() or str(uuid.uuid4())
    title = str(doc.get("title", "")).strip()
    content = str(doc.get("content", "")).strip()
    knowledge_type = str(doc.get("knowledge_type", "")).strip().lower()
    valid_types = {"attack_technique", "cve", "term", "tool"}
    if not title:
        raise ValueError("安全知识文档必须包含 title")
    if not content:
        raise ValueError("安全知识文档必须包含 content")
    if knowledge_type not in valid_types:
        raise ValueError(f"knowledge_type 仅支持 {sorted(valid_types)}，收到: {knowledge_type}")

    return {
        "id": item_id,
        "title": title,
        "content": content,
        "knowledge_type": knowledge_type,
    }


def _build_qdrant_point_id(raw_id: Any) -> Any:
    value = str(raw_id or "").strip()
    if value:
        if value.isdigit():
            return int(value)
        try:
            return str(uuid.UUID(value))
        except ValueError:
            return str(uuid.uuid5(uuid.NAMESPACE_URL, f"qdrant-point:{value}"))
    return str(uuid.uuid4())


def _build_case_points(documents: List[Dict[str, Any]], vector_size: int) -> List[PointStruct]:
    payloads = [_normalize_case_doc(doc) for doc in documents]
    # 仅将 alert_payload 放入向量空间
    embedding_texts = [_serialize_text_fragment(payload.get("alert_payload")) for payload in payloads]
    vectors = _build_offline_vectors(texts=embedding_texts, vector_size=vector_size)
    return [
        PointStruct(
            id=_build_qdrant_point_id(payload.get("id")),
            vector=vector,
            payload=payload,
        )
        for payload, vector in zip(payloads, vectors)
    ]


def _build_security_points(documents: List[Dict[str, Any]], vector_size: int) -> List[PointStruct]:
    payloads = [_normalize_security_doc(doc) for doc in documents]
    # 仅将 title 放入向量空间
    embedding_texts = [_serialize_text_fragment(payload.get("title")) for payload in payloads]
    vectors = _build_offline_vectors(texts=embedding_texts, vector_size=vector_size)
    return [
        PointStruct(
            id=_build_qdrant_point_id(payload.get("id")),
            vector=vector,
            payload=payload,
        )
        for payload, vector in zip(payloads, vectors)
    ]


def run_qdrant_init(args: argparse.Namespace) -> None:
    from app.services.knowledge_base_service import (
        DEFAULT_CASE_SEED_DOCUMENTS,
        DEFAULT_SECURITY_KNOWLEDGE_SEED_DOCUMENTS,
        KnowledgeBaseService,
    )

    service = KnowledgeBaseService()
    case_docs = load_seed_documents(args.cases_file, DEFAULT_CASE_SEED_DOCUMENTS)
    security_docs = load_seed_documents(args.security_file, DEFAULT_SECURITY_KNOWLEDGE_SEED_DOCUMENTS)

    cases_collection = args.cases_collection.strip() or service.cases_collection
    security_collection = args.security_collection.strip() or service.security_knowledge_collection

    vector_size = int(service.vector_size)

    service.ensure_collection(cases_collection, recreate=args.recreate_cases)
    case_points = _build_case_points(documents=case_docs, vector_size=vector_size)
    if case_points:
        service.client.upsert(collection_name=cases_collection, points=case_points, wait=True)

    service.ensure_collection(security_collection, recreate=args.recreate_security)
    security_points = _build_security_points(documents=security_docs, vector_size=vector_size)
    if security_points:
        service.client.upsert(collection_name=security_collection, points=security_points, wait=True)

    print("=== Qdrant Vector Knowledge Init Result ===")
    print(f"qdrant_url: {service.qdrant_url}")
    print("embedding_provider: offline_bm25_ngram_random_indexing")
    print(f"vector_size: {vector_size}")
    print("vector_sources: kb_cases.alert_payload, kb_security_knowledge.title")
    print(f"cases_collection: {cases_collection}, upserted: {len(case_points)}")
    print(f"security_collection: {security_collection}, upserted: {len(security_points)}")


def main() -> int:
    args = build_parser().parse_args()

    if args.target in {"sqlite", "all"}:
        run_sqlite_init(args)
    if args.target in {"qdrant", "all"}:
        run_qdrant_init(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
