from typing import Any, Dict

from fastmcp import FastMCP

from app.services.knowledge_base_service import KnowledgeBaseService

mcp = FastMCP("cases_kb_mcp")


@mcp.tool(name="case_semantic_search", description="语义检索历史案例（kb_cases）")
def case_semantic_search(
    query_text: str,
    limit: int = 3,
    alert_type: str = "",
    severity: str = "",
    score_threshold: float = 0.9,
) -> Dict[str, Any]:
    service = KnowledgeBaseService()
    safe_limit = max(1, min(int(limit), 20))
    items = service.search_case_knowledge(
        query_text=str(query_text or "").strip(),
        alert_type=str(alert_type or "").strip() or None,
        severity=str(severity or "").strip() or None,
        limit=safe_limit,
        score_threshold=float(score_threshold),
    )
    return {
        "collection": service.cases_collection,
        "query_text": str(query_text or "").strip(),
        "score_threshold": float(score_threshold),
        "count": len(items),
        "items": items,
    }


@mcp.tool(name="case_lookup_by_id", description="按案例ID精确查询历史案例（kb_cases）")
def case_lookup_by_id(case_id: str) -> Dict[str, Any]:
    service = KnowledgeBaseService()
    target_id = str(case_id or "").strip()
    if not target_id:
        return {"found": False, "message": "case_id不能为空"}

    items = service.search_knowledge(
        query_text=target_id,
        collection_name=service.cases_collection,
        limit=1,
        payload_filters={"id": target_id},
        score_threshold=None,
    )
    if not items:
        return {"found": False, "case_id": target_id, "collection": service.cases_collection}

    return {
        "found": True,
        "case_id": target_id,
        "collection": service.cases_collection,
        "item": items[0],
    }
