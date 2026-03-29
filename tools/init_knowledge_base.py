#!/usr/bin/env python3
"""Initialize knowledge base documents into database + Qdrant.

Usage:
    python3 tools/init_knowledge_base.py
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv
from flask import Flask

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env", override=True)

from app.models.models import KBDocument, Prompt, db
from app.services.knowledge_base_service import KB_TYPES, get_knowledge_base_service

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv(
    'DATABASE_URL',
    'sqlite:////Users/sssu/Project/deepsoc/deepsoc.db',
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)


def _load_prompt_docs() -> List[Dict]:
    docs: List[Dict] = []
    prompt_mapping = [
        ('background_security', 'kb_security_knowledge', '安全背景知识'),
        ('background_soar_playbooks', 'kb_playbook', 'SOAR流程背景'),
        ('mcp_tools', 'kb_context', 'MCP工具能力清单'),
    ]
    for prompt_name, kb_type, title in prompt_mapping:
        row = Prompt.query.filter_by(name=prompt_name).first()
        if not row or not (row.content or '').strip():
            continue
        docs.append({
            'doc_id': f'prompt_{prompt_name}',
            'tenant_id': 'default',
            'kb_type': kb_type,
            'title': title,
            'content': row.content.strip(),
            'source': f'prompt:{prompt_name}',
            'tags': ['prompt_bootstrap'],
            'metadata': {'prompt_name': prompt_name},
            'status': 'published',
            'reindex': True,
        })
    return docs


def _load_file_docs(base_dir: Path) -> List[Dict]:
    docs: List[Dict] = []
    if not base_dir.exists():
        return docs

    for path in sorted(base_dir.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception as exc:
            print(f"[WARN] Skip invalid json: {path.name}, err={exc}")
            continue

        rel_path = path.relative_to(base_dir)
        folder_kb_type = rel_path.parts[0] if len(rel_path.parts) > 1 else ''
        inferred_kb_type = folder_kb_type if folder_kb_type in KB_TYPES else ''

        items = payload if isinstance(payload, list) else [payload]
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue

            kb_type = str(item.get('kb_type') or inferred_kb_type).strip()
            title = str(item.get('title') or '').strip()
            content = str(item.get('content') or '').strip()
            if kb_type not in KB_TYPES:
                print(f"[WARN] Skip doc in {path.name}: invalid kb_type={kb_type}")
                continue
            if inferred_kb_type and kb_type != inferred_kb_type:
                print(
                    f"[WARN] {path.name} kb_type mismatch. "
                    f"use folder type '{inferred_kb_type}' instead of '{kb_type}'."
                )
                kb_type = inferred_kb_type
            if not title or not content:
                print(f"[WARN] Skip doc in {path.name}: title/content missing")
                continue

            doc = dict(item)
            doc['kb_type'] = kb_type
            doc.setdefault('doc_id', f"{kb_type}_{path.stem}_{index + 1}")
            doc.setdefault('tenant_id', 'default')
            doc.setdefault('source', f"file:{rel_path.as_posix()}")
            doc.setdefault('tags', [])
            doc.setdefault('metadata', {})
            doc.setdefault('status', 'published')
            doc.setdefault('reindex', True)
            docs.append(doc)
    return docs


def main():
    kb_dir = ROOT_DIR / 'data' / 'knowledge_base'
    with app.app_context():
        KBDocument.__table__.create(bind=db.engine, checkfirst=True)
        kb_service = get_knowledge_base_service()
        docs = []
        docs.extend(_load_prompt_docs())
        docs.extend(_load_file_docs(kb_dir))

        if not docs:
            print("No knowledge documents found to import.")
            return

        success = 0
        for doc in docs:
            try:
                kb_service.upsert_document(payload=doc, updated_by='kb_init_script')
                success += 1
            except Exception as exc:
                print(f"[ERROR] Import failed: title={doc.get('title')}, err={exc}")

        print(f"Knowledge base import finished: {success}/{len(docs)}")


if __name__ == '__main__':
    main()
