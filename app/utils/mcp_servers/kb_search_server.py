from typing import Dict, List, Optional

from fastmcp import FastMCP

from app.services.knowledge_base_service import (
    KB_TYPES,
    ROLE_DEFAULT_KB_TYPES,
    get_knowledge_base_service,
    normalize_kb_types,
)

mcp = FastMCP("knowledge_base_mcp")


def _clean_tenant_id(tenant_id: Optional[str]) -> Optional[str]:
    value = (tenant_id or "").strip()
    return value or None


def _normalize_threshold(score_threshold: Optional[float]) -> Optional[float]:
    if score_threshold is None:
        return None
    try:
        threshold = float(score_threshold)
    except Exception:
        return None
    if threshold <= 0:
        return None
    return threshold


@mcp.tool(
    name="kb_search",
    description="检索知识库（支持按 kb_types 指定分类，如 kb_playbook,kb_context）",
)
def kb_search(
    query: str,
    kb_types: str = "",
    tenant_id: str = "default",
    top_k: int = 6,
    score_threshold: float = 0.0,
) -> Dict[str, object]:
    try:
        selected_types: Optional[List[str]] = None
        if kb_types.strip():
            selected_types = normalize_kb_types(kb_types)

        kb_service = get_knowledge_base_service()
        result = kb_service.build_kb_context(
            query=query,
            tenant_id=_clean_tenant_id(tenant_id),
            kb_types=selected_types,
            top_k=top_k,
            score_threshold=_normalize_threshold(score_threshold),
        )
        return {
            "status": "success",
            "query": query,
            "tenant_id": _clean_tenant_id(tenant_id) or kb_service.default_tenant_id,
            "kb_types": selected_types or KB_TYPES,
            "hits": result.get("hits", []),
            "kb_refs": result.get("kb_refs", []),
            "kb_context": result.get("kb_context", ""),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "message": f"kb_search failed: {exc}",
        }


@mcp.tool(
    name="kb_search_for_role",
    description="按角色默认知识分类检索（role 支持 _captain/_manager/_operator/_expert）",
)
def kb_search_for_role(
    role: str,
    query: str,
    tenant_id: str = "default",
    top_k: int = 6,
) -> Dict[str, object]:
    role_name = (role or "").strip()
    role_kb_types = ROLE_DEFAULT_KB_TYPES.get(role_name)
    if not role_kb_types:
        return {
            "status": "failed",
            "message": f"unsupported role: {role_name}",
            "supported_roles": list(ROLE_DEFAULT_KB_TYPES.keys()),
        }

    try:
        kb_service = get_knowledge_base_service()
        result = kb_service.build_kb_context(
            query=query,
            tenant_id=_clean_tenant_id(tenant_id),
            kb_types=role_kb_types,
            top_k=top_k,
        )
        return {
            "status": "success",
            "role": role_name,
            "tenant_id": _clean_tenant_id(tenant_id) or kb_service.default_tenant_id,
            "kb_types": role_kb_types,
            "hits": result.get("hits", []),
            "kb_refs": result.get("kb_refs", []),
            "kb_context": result.get("kb_context", ""),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "message": f"kb_search_for_role failed: {exc}",
        }
