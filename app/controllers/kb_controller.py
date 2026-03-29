import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services.knowledge_base_service import (
    KB_TYPES,
    get_knowledge_base_service,
    normalize_kb_types,
)

kb_bp = Blueprint('kb', __name__)
logger = logging.getLogger(__name__)


def _parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_tenant_id(data=None):
    data = data or {}
    tenant_id = (
        request.args.get('tenant_id')
        or data.get('tenant_id')
        or ''
    )
    return tenant_id.strip() or None


@kb_bp.route('/types', methods=['GET'])
@jwt_required()
def list_kb_types():
    return jsonify({
        'status': 'success',
        'data': {
            'kb_types': KB_TYPES
        }
    })


@kb_bp.route('/documents', methods=['GET'])
@jwt_required()
def list_documents():
    kb_service = get_knowledge_base_service()
    kb_type = (request.args.get('kb_type') or '').strip() or None
    limit = request.args.get('limit', 100, type=int)
    tenant_id = _resolve_tenant_id()

    try:
        rows = kb_service.list_documents(
            tenant_id=tenant_id,
            kb_type=kb_type,
            limit=limit,
        )
        return jsonify({'status': 'success', 'data': rows})
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400
    except Exception as exc:
        logger.error(f"list_documents failed: {exc}")
        return jsonify({'status': 'error', 'message': '查询知识库文档失败'}), 500


@kb_bp.route('/documents', methods=['POST'])
@jwt_required()
def upsert_document():
    kb_service = get_knowledge_base_service()
    payload = request.get_json() or {}
    tenant_id = _resolve_tenant_id(payload)
    if tenant_id:
        payload['tenant_id'] = tenant_id

    updated_by = get_jwt_identity()

    try:
        data = kb_service.upsert_document(payload=payload, updated_by=updated_by)
        return jsonify({'status': 'success', 'data': data})
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400
    except Exception as exc:
        logger.error(f"upsert_document failed: {exc}")
        return jsonify({'status': 'error', 'message': '保存知识库文档失败'}), 500


@kb_bp.route('/documents/<doc_id>', methods=['GET'])
@jwt_required()
def get_document(doc_id):
    kb_service = get_knowledge_base_service()
    tenant_id = _resolve_tenant_id()
    try:
        row = kb_service.get_document(doc_id=doc_id, tenant_id=tenant_id)
        if not row:
            return jsonify({'status': 'error', 'message': '文档不存在'}), 404
        return jsonify({'status': 'success', 'data': row})
    except Exception as exc:
        logger.error(f"get_document failed: {exc}")
        return jsonify({'status': 'error', 'message': '获取知识库文档失败'}), 500


@kb_bp.route('/documents/<doc_id>', methods=['PUT'])
@jwt_required()
def update_document(doc_id):
    kb_service = get_knowledge_base_service()
    payload = request.get_json() or {}
    payload['doc_id'] = doc_id
    tenant_id = _resolve_tenant_id(payload)
    if tenant_id:
        payload['tenant_id'] = tenant_id

    updated_by = get_jwt_identity()

    try:
        data = kb_service.upsert_document(payload=payload, updated_by=updated_by)
        return jsonify({'status': 'success', 'data': data})
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400
    except Exception as exc:
        logger.error(f"update_document failed: {exc}")
        return jsonify({'status': 'error', 'message': '更新知识库文档失败'}), 500


@kb_bp.route('/documents/<doc_id>', methods=['DELETE'])
@jwt_required()
def delete_document(doc_id):
    kb_service = get_knowledge_base_service()
    tenant_id = _resolve_tenant_id()
    purge_vectors = _parse_bool(request.args.get('purge_vectors'), default=True)

    try:
        result = kb_service.delete_document(
            doc_id=doc_id,
            tenant_id=tenant_id,
            purge_vectors=purge_vectors,
        )
        if not result:
            return jsonify({'status': 'error', 'message': '文档不存在'}), 404
        return jsonify({'status': 'success', 'data': result})
    except Exception as exc:
        logger.error(f"delete_document failed: {exc}")
        return jsonify({'status': 'error', 'message': '删除知识库文档失败'}), 500


@kb_bp.route('/documents/<doc_id>/reindex', methods=['POST'])
@jwt_required()
def reindex_document(doc_id):
    kb_service = get_knowledge_base_service()
    payload = request.get_json() or {}
    tenant_id = _resolve_tenant_id(payload)
    try:
        data = kb_service.reindex_document(doc_id=doc_id, tenant_id=tenant_id)
        return jsonify({'status': 'success', 'data': data})
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400
    except Exception as exc:
        logger.error(f"reindex_document failed: {exc}")
        return jsonify({'status': 'error', 'message': '重建向量索引失败'}), 500


@kb_bp.route('/search', methods=['POST'])
@jwt_required()
def search_kb():
    kb_service = get_knowledge_base_service()
    payload = request.get_json() or {}
    query = (payload.get('query') or '').strip()
    if not query:
        return jsonify({'status': 'error', 'message': 'query不能为空'}), 400

    tenant_id = _resolve_tenant_id(payload)
    kb_types = payload.get('kb_types')
    top_k = payload.get('top_k')
    score_threshold = payload.get('score_threshold')
    tags = payload.get('tags')

    try:
        normalized_types = normalize_kb_types(kb_types) if kb_types else None
        result = kb_service.build_kb_context(
            query=query,
            tenant_id=tenant_id,
            kb_types=normalized_types,
            top_k=top_k,
            score_threshold=score_threshold,
            tags=tags,
        )
        return jsonify({
            'status': 'success',
            'data': {
                'query': query,
                'tenant_id': tenant_id or kb_service.default_tenant_id,
                'kb_types': normalized_types or KB_TYPES,
                'hits': result.get('hits', []),
                'kb_refs': result.get('kb_refs', []),
                'kb_context': result.get('kb_context', ''),
            }
        })
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400
    except Exception as exc:
        logger.error(f"search_kb failed: {exc}")
        return jsonify({'status': 'error', 'message': '知识库检索失败'}), 500
