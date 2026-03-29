import hashlib
import math
import os
import re
import uuid
from typing import Any, Dict, List, Optional

import requests

from app.models import db
from app.models.models import KBDocument

import logging

logger = logging.getLogger(__name__)

KB_TYPES = [
    'kb_playbook',
    'kb_context',
    'kb_cases',
    'kb_security_knowledge',
    'kb_policy_personal',
]

ROLE_DEFAULT_KB_TYPES = {
    '_captain': ['kb_policy_personal', 'kb_context', 'kb_cases'],
    '_manager': ['kb_policy_personal', 'kb_playbook', 'kb_context'],
    '_operator': ['kb_policy_personal', 'kb_playbook', 'kb_context'],
    '_expert': ['kb_policy_personal', 'kb_cases', 'kb_security_knowledge', 'kb_context'],
}


def normalize_kb_types(kb_types):
    if not kb_types:
        return list(KB_TYPES)

    if isinstance(kb_types, str):
        items = [item.strip() for item in kb_types.split(',') if item.strip()]
    elif isinstance(kb_types, (list, tuple, set)):
        items = [str(item).strip() for item in kb_types if str(item).strip()]
    else:
        raise ValueError('kb_types must be list or comma-separated string')

    result = []
    seen = set()
    for item in items:
        if item not in KB_TYPES:
            raise ValueError(f'unsupported kb_type: {item}')
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _join_query_parts(parts: List[Any]) -> str:
    items = [str(item).strip() for item in parts if str(item).strip()]
    return "\n".join(items)


class KnowledgeBaseService:
    """Knowledge base service backed by SQLAlchemy + Qdrant REST API."""

    def __init__(self):
        qdrant_url = os.getenv('QDRANT_URL', '').strip()
        if qdrant_url:
            self.qdrant_url = qdrant_url.rstrip('/')
        else:
            host = os.getenv('QDRANT_HOST', '127.0.0.1')
            port = os.getenv('QDRANT_PORT', '6333')
            self.qdrant_url = f"http://{host}:{port}"

        self.qdrant_timeout = int(os.getenv('KB_QDRANT_TIMEOUT', 15))
        self.collection_prefix = os.getenv('KB_COLLECTION_PREFIX', 'deepsoc_')
        self.embedding_dim = int(os.getenv('KB_EMBEDDING_DIM', 256))
        self.chunk_size = int(os.getenv('KB_CHUNK_SIZE', 800))
        self.chunk_overlap = int(os.getenv('KB_CHUNK_OVERLAP', 120))
        self.default_top_k = int(os.getenv('KB_TOP_K', 8))
        self.default_tenant_id = os.getenv('KB_DEFAULT_TENANT', 'default')
        threshold = os.getenv('KB_SCORE_THRESHOLD', '').strip()
        self.default_score_threshold = float(threshold) if threshold else None
        cases_threshold = os.getenv('KB_CASES_HIT_SCORE_THRESHOLD', '0.65').strip()
        self.captain_cases_hit_score_threshold = float(cases_threshold) if cases_threshold else None
        self.distance = os.getenv('KB_DISTANCE', 'Cosine')

    def _request(self, method: str, path: str, json_body: Optional[Dict[str, Any]] = None):
        url = f"{self.qdrant_url}{path}"
        try:
            response = requests.request(
                method=method,
                url=url,
                json=json_body,
                timeout=self.qdrant_timeout,
            )
            return response
        except Exception as exc:
            logger.error(f"Qdrant request failed: {method} {url}, error={exc}")
            raise

    def _collection_name(self, kb_type: str) -> str:
        env_key = f"KB_COLLECTION_{kb_type.upper()}"
        collection_name = os.getenv(env_key, '').strip()
        if collection_name:
            return collection_name
        return f"{self.collection_prefix}{kb_type}"

    def _ensure_collection(self, kb_type: str):
        collection_name = self._collection_name(kb_type)
        body = {
            "vectors": {
                "size": self.embedding_dim,
                "distance": self.distance,
            }
        }
        response = self._request(
            "PUT",
            f"/collections/{collection_name}",
            json_body=body,
        )
        if response.status_code == 409:
            return collection_name
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"failed to ensure qdrant collection {collection_name}: "
                f"status={response.status_code}, body={response.text}"
            )
        return collection_name

    def _tokenize(self, text: str) -> List[str]:
        if not text:
            return []
        return re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+", text.lower())

    def _embed_text(self, text: str) -> List[float]:
        vector = [0.0] * self.embedding_dim
        tokens = self._tokenize(text)
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode('utf-8')).digest()
            index = int.from_bytes(digest[:4], byteorder='big', signed=False) % self.embedding_dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector

    def _chunk_text(self, text: str) -> List[str]:
        text = (text or '').strip()
        if not text:
            return []
        if len(text) <= self.chunk_size:
            return [text]

        step = max(1, self.chunk_size - self.chunk_overlap)
        chunks: List[str] = []
        for start in range(0, len(text), step):
            piece = text[start:start + self.chunk_size].strip()
            if not piece:
                continue
            chunks.append(piece)
            if start + self.chunk_size >= len(text):
                break
        return chunks

    def upsert_document(self, payload: Dict[str, Any], updated_by: Optional[str] = None):
        kb_type = payload.get('kb_type')
        if kb_type not in KB_TYPES:
            raise ValueError(f'kb_type must be one of: {", ".join(KB_TYPES)}')

        title = (payload.get('title') or '').strip()
        content = (payload.get('content') or '').strip()
        if not title:
            raise ValueError('title is required')
        if not content:
            raise ValueError('content is required')

        tenant_id = (payload.get('tenant_id') or self.default_tenant_id).strip()
        if not tenant_id:
            tenant_id = self.default_tenant_id

        doc_id = (payload.get('doc_id') or '').strip()
        source = (payload.get('source') or '').strip()
        status = (payload.get('status') or 'published').strip()
        if status not in ('draft', 'published', 'deprecated'):
            raise ValueError('status must be draft/published/deprecated')

        raw_tags = payload.get('tags') or []
        if isinstance(raw_tags, str):
            tags = [item.strip() for item in raw_tags.split(',') if item.strip()]
        elif isinstance(raw_tags, list):
            tags = [str(item).strip() for item in raw_tags if str(item).strip()]
        else:
            raise ValueError('tags must be list or comma-separated string')

        metadata = payload.get('metadata') or {}
        if not isinstance(metadata, dict):
            raise ValueError('metadata must be object')

        confidence = payload.get('confidence', 0.8)
        try:
            confidence = float(confidence)
        except Exception as exc:
            raise ValueError(f'invalid confidence: {exc}')

        document = None
        if doc_id:
            document = KBDocument.query.filter_by(doc_id=doc_id, tenant_id=tenant_id).first()
        if not document:
            document = KBDocument(
                doc_id=doc_id or str(uuid.uuid4()),
                tenant_id=tenant_id,
                created_by=updated_by,
            )
            db.session.add(document)

        document.kb_type = kb_type
        document.title = title
        document.content = content
        document.source = source
        document.tags = tags
        document.doc_meta = metadata
        document.status = status
        document.confidence = confidence
        document.updated_by = updated_by
        if document.id is not None:
            document.version = (document.version or 1) + 1

        db.session.commit()

        reindex = bool(payload.get('reindex', True))
        index_info = None
        if reindex and document.status != 'deprecated':
            index_info = self.reindex_document(document.doc_id, tenant_id=document.tenant_id)

        result = document.to_dict()
        if index_info is not None:
            result['index_info'] = index_info
        return result

    def get_document(self, doc_id: str, tenant_id: Optional[str] = None):
        target_tenant = tenant_id or self.default_tenant_id
        document = KBDocument.query.filter_by(doc_id=doc_id, tenant_id=target_tenant).first()
        if not document:
            return None
        return document.to_dict()

    def list_documents(self, tenant_id: Optional[str] = None, kb_type: Optional[str] = None, limit: int = 100):
        query = KBDocument.query
        target_tenant = tenant_id or self.default_tenant_id
        query = query.filter_by(tenant_id=target_tenant)
        if kb_type:
            if kb_type not in KB_TYPES:
                raise ValueError(f'unsupported kb_type: {kb_type}')
            query = query.filter_by(kb_type=kb_type)
        rows = query.order_by(KBDocument.updated_at.desc()).limit(max(1, min(limit, 500))).all()
        return [row.to_dict() for row in rows]

    def delete_document(self, doc_id: str, tenant_id: Optional[str] = None, purge_vectors: bool = True):
        target_tenant = tenant_id or self.default_tenant_id
        document = KBDocument.query.filter_by(doc_id=doc_id, tenant_id=target_tenant).first()
        if not document:
            return None

        collection_name = self._collection_name(document.kb_type)
        if purge_vectors:
            try:
                self._delete_document_points(
                    collection_name=collection_name,
                    doc_id=document.doc_id,
                    tenant_id=document.tenant_id,
                    ignore_not_found=True,
                )
            except Exception as exc:
                logger.warning(
                    f"delete vector points failed for doc_id={document.doc_id}, "
                    f"collection={collection_name}, error={exc}"
                )

        result = {
            "doc_id": document.doc_id,
            "tenant_id": document.tenant_id,
            "kb_type": document.kb_type,
            "collection": collection_name,
        }
        db.session.delete(document)
        db.session.commit()
        return result

    def _delete_document_points(self, collection_name: str, doc_id: str, tenant_id: str, ignore_not_found: bool = False):
        body = {
            "filter": {
                "must": [
                    {"key": "doc_id", "match": {"value": doc_id}},
                    {"key": "tenant_id", "match": {"value": tenant_id}},
                ]
            }
        }
        response = self._request(
            "POST",
            f"/collections/{collection_name}/points/delete?wait=true",
            json_body=body,
        )
        if response.status_code == 404 and ignore_not_found:
            return
        if response.status_code not in (200, 202):
            raise RuntimeError(
                f"failed to delete old points in {collection_name}: "
                f"status={response.status_code}, body={response.text}"
            )

    def _upload_points(self, collection_name: str, points: List[Dict[str, Any]]):
        if not points:
            return
        body = {"points": points}
        response = self._request(
            "PUT",
            f"/collections/{collection_name}/points?wait=true",
            json_body=body,
        )
        if response.status_code not in (200, 201, 202):
            raise RuntimeError(
                f"failed to upsert points in {collection_name}: "
                f"status={response.status_code}, body={response.text}"
            )

    def reindex_document(self, doc_id: str, tenant_id: Optional[str] = None):
        target_tenant = tenant_id or self.default_tenant_id
        document = KBDocument.query.filter_by(doc_id=doc_id, tenant_id=target_tenant).first()
        if not document:
            raise ValueError('document not found')
        if document.kb_type not in KB_TYPES:
            raise ValueError(f"unsupported kb_type: {document.kb_type}")

        collection_name = self._ensure_collection(document.kb_type)
        self._delete_document_points(collection_name, document.doc_id, document.tenant_id)

        chunks = self._chunk_text(document.content)
        points = []
        for index, chunk in enumerate(chunks):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document.doc_id}:{index}"))
            payload = {
                "tenant_id": document.tenant_id,
                "kb_type": document.kb_type,
                "doc_id": document.doc_id,
                "chunk_index": index,
                "title": document.title,
                "text": chunk,
                "tags": document.tags or [],
                "source": document.source or '',
                "status": document.status,
                "confidence": document.confidence,
                "version": document.version,
            }
            points.append({
                "id": point_id,
                "vector": self._embed_text(chunk),
                "payload": payload,
            })

        batch_size = 64
        for idx in range(0, len(points), batch_size):
            self._upload_points(collection_name, points[idx:idx + batch_size])

        return {
            "collection": collection_name,
            "chunks": len(chunks),
            "doc_id": document.doc_id,
        }

    def search(
        self,
        query: str,
        tenant_id: Optional[str] = None,
        kb_types: Optional[List[str]] = None,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
        tags: Optional[List[str]] = None,
    ):
        query_text = (query or '').strip()
        if not query_text:
            return []

        target_tenant = (tenant_id or self.default_tenant_id).strip() or self.default_tenant_id
        target_types = normalize_kb_types(kb_types)
        limit = int(top_k or self.default_top_k)
        limit = max(1, min(limit, 50))
        threshold = self.default_score_threshold if score_threshold is None else float(score_threshold)
        query_vector = self._embed_text(query_text)

        all_hits: List[Dict[str, Any]] = []
        normalized_tags = []
        if tags:
            normalized_tags = [str(item).strip() for item in tags if str(item).strip()]

        for kb_type in target_types:
            collection_name = self._collection_name(kb_type)
            must_conditions = [
                {"key": "tenant_id", "match": {"value": target_tenant}},
                {"key": "status", "match": {"value": "published"}},
            ]
            filter_body: Dict[str, Any] = {"must": must_conditions}
            if normalized_tags:
                filter_body["should"] = [
                    {"key": "tags", "match": {"value": tag}} for tag in normalized_tags
                ]

            body = {
                "vector": query_vector,
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
                "filter": filter_body,
            }
            if threshold is not None:
                body["score_threshold"] = threshold

            try:
                response = self._request(
                    "POST",
                    f"/collections/{collection_name}/points/search",
                    json_body=body,
                )
            except Exception as exc:
                logger.warning(
                    f"Qdrant search request failed for collection={collection_name}, "
                    f"error={exc}"
                )
                continue
            if response.status_code == 404:
                logger.info(f"Qdrant collection not found, skip search: {collection_name}")
                continue
            if response.status_code not in (200, 201):
                logger.warning(
                    f"Qdrant search failed for collection={collection_name}, "
                    f"status={response.status_code}, body={response.text}"
                )
                continue

            result = response.json().get('result', [])
            for item in result:
                payload = item.get('payload', {}) or {}
                all_hits.append({
                    "score": item.get('score', 0.0),
                    "kb_type": payload.get('kb_type', kb_type),
                    "doc_id": payload.get('doc_id'),
                    "chunk_index": payload.get('chunk_index'),
                    "title": payload.get('title'),
                    "text": payload.get('text'),
                    "source": payload.get('source'),
                    "tags": payload.get('tags') or [],
                    "collection": collection_name,
                })

        all_hits.sort(key=lambda item: item.get('score', 0.0), reverse=True)
        return all_hits[:limit]

    def build_kb_context(
        self,
        query: str,
        tenant_id: Optional[str] = None,
        kb_types: Optional[List[str]] = None,
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
        tags: Optional[List[str]] = None,
    ):
        hits = self.search(
            query=query,
            tenant_id=tenant_id,
            kb_types=kb_types,
            top_k=top_k,
            score_threshold=score_threshold,
            tags=tags,
        )
        if not hits:
            return {"kb_context": "", "kb_refs": [], "hits": []}

        lines = []
        refs = []
        for idx, hit in enumerate(hits, start=1):
            ref_id = f"KB{idx}"
            title = hit.get('title') or 'Untitled'
            text = hit.get('text') or ''
            score = float(hit.get('score') or 0.0)
            kb_type = hit.get('kb_type')
            doc_id = hit.get('doc_id')
            chunk_index = hit.get('chunk_index')

            lines.append(f"[{ref_id}] ({kb_type}) {title} score={score:.4f}")
            lines.append(text)
            lines.append("")

            refs.append({
                "ref_id": ref_id,
                "kb_type": kb_type,
                "doc_id": doc_id,
                "chunk_index": chunk_index,
                "title": title,
                "score": score,
            })

        return {
            "kb_context": "\n".join(lines).strip(),
            "kb_refs": refs,
            "hits": hits,
        }

    def get_policy_context(self, query: str, tenant_id: Optional[str] = None, top_k: int = 5):
        return self.build_kb_context(
            query=query,
            tenant_id=tenant_id,
            kb_types=['kb_policy_personal'],
            top_k=top_k,
        )

    def build_role_kb_context(
        self,
        role: str,
        query_parts: List[Any],
        tenant_id: Optional[str] = None,
        kb_types: Optional[List[str]] = None,
        top_k: int = 6,
    ):
        query_text = _join_query_parts(query_parts)
        if not query_text:
            return {"kb_context": "", "kb_refs": [], "hits": []}

        selected_types = kb_types or ROLE_DEFAULT_KB_TYPES.get(role) or None
        if selected_types:
            selected_types = normalize_kb_types(selected_types)

        return self.build_kb_context(
            query=query_text,
            tenant_id=tenant_id,
            kb_types=selected_types,
            top_k=top_k,
        )

    def render_role_kb_prompt_block(
        self,
        role: str,
        query_parts: List[Any],
        tenant_id: Optional[str] = None,
        kb_types: Optional[List[str]] = None,
        top_k: int = 6,
    ) -> str:
        result = self.build_role_kb_context(
            role=role,
            query_parts=query_parts,
            tenant_id=tenant_id,
            kb_types=kb_types,
            top_k=top_k,
        )
        kb_context = (result.get("kb_context") or "").strip()
        if not kb_context:
            return ""

        return (
            "以下是为当前研判自动召回的知识库片段（按相关性排序）：\n"
            "<kb_context>\n"
            f"{kb_context}\n"
            "</kb_context>\n"
            "要求：优先依据以上知识进行溯源判断；若与实时查询结果冲突，以实时结果为准。"
        )

    def build_captain_first_round_kb_bundle(
        self,
        query_parts: List[Any],
        tenant_id: Optional[str] = None,
        top_k: int = 6,
    ) -> Dict[str, Any]:
        query_text = _join_query_parts(query_parts)
        if not query_text:
            return {
                "query": "",
                "initial": {"hits": [], "kb_context": "", "kb_refs": []},
                "cases": {"hits": [], "kb_context": "", "kb_refs": []},
                "playbook": {"hits": [], "kb_context": "", "kb_refs": []},
                "decision_source": "",
            }

        initial = self.build_kb_context(
            query=query_text,
            tenant_id=tenant_id,
            kb_types=['kb_security_knowledge', 'kb_context'],
            top_k=top_k,
        )
        cases = self.build_kb_context(
            query=query_text,
            tenant_id=tenant_id,
            kb_types=['kb_cases'],
            top_k=top_k,
            score_threshold=self.captain_cases_hit_score_threshold,
        )

        playbook = {"hits": [], "kb_context": "", "kb_refs": []}
        if cases.get('hits'):
            decision_source = 'kb_cases'
        else:
            playbook = self.build_kb_context(
                query=query_text,
                tenant_id=tenant_id,
                kb_types=['kb_playbook'],
                top_k=top_k,
            )
            decision_source = 'kb_playbook' if playbook.get('hits') else 'none'

        return {
            "query": query_text,
            "initial": initial,
            "cases": cases,
            "playbook": playbook,
            "decision_source": decision_source,
            "cases_score_threshold": self.captain_cases_hit_score_threshold,
        }

    def render_captain_first_round_prompt_block(
        self,
        query_parts: List[Any],
        tenant_id: Optional[str] = None,
        top_k: int = 6,
    ):
        bundle = self.build_captain_first_round_kb_bundle(
            query_parts=query_parts,
            tenant_id=tenant_id,
            top_k=top_k,
        )
        lines = []

        initial_ctx = (bundle["initial"].get("kb_context") or "").strip()
        if initial_ctx:
            lines.append("【首轮步骤1】先参考 `kb_security_knowledge + kb_context` 做初始研判：")
            lines.append("<kb_initial_assessment>")
            lines.append(initial_ctx)
            lines.append("</kb_initial_assessment>")
            lines.append("")

        cases_ctx = (bundle["cases"].get("kb_context") or "").strip()
        if cases_ctx:
            lines.append("【首轮步骤2】再参考 `kb_cases` 历史研判：")
            lines.append("<kb_cases_history>")
            lines.append(cases_ctx)
            lines.append("</kb_cases_history>")
            lines.append("")
            lines.append("要求：优先沿用历史可复用的研判逻辑，并说明差异。")
        else:
            lines.append("【首轮步骤2】`kb_cases` 未检索到高相关历史。")
            lines.append("")
            playbook_ctx = (bundle["playbook"].get("kb_context") or "").strip()
            if playbook_ctx:
                lines.append("【首轮步骤3】改为参考 `kb_playbook` 的 SOP 流程：")
                lines.append("<kb_playbook_sop>")
                lines.append(playbook_ctx)
                lines.append("</kb_playbook_sop>")
                lines.append("")
                lines.append("要求：按 SOP 优先顺序生成本轮任务。")
            else:
                lines.append("【首轮步骤3】`kb_playbook` 也未命中，请按通用安全研判流程谨慎制定任务。")

        prompt_block = "\n".join(lines).strip()
        return prompt_block, bundle


_KB_SERVICE_INSTANCE: Optional[KnowledgeBaseService] = None


def get_knowledge_base_service():
    global _KB_SERVICE_INSTANCE
    if _KB_SERVICE_INSTANCE is None:
        _KB_SERVICE_INSTANCE = KnowledgeBaseService()
    return _KB_SERVICE_INSTANCE
