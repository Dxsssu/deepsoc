from typing import Any, Dict

from fastmcp import FastMCP

from app.services.knowledge_base_service import KnowledgeBaseService

mcp = FastMCP("security_knowledge_kb_mcp")


@mcp.tool(name="security_knowledge_search", description="语义检索安全知识/术语（kb_security_knowledge）")
def security_knowledge_search(
    query_text: str,
    knowledge_type: str = "",
    limit: int = 3,
    score_threshold: float = 0.9,
) -> Dict[str, Any]:
    service = KnowledgeBaseService()
    safe_limit = max(1, min(int(limit), 20))
    items = service.search_security_knowledge(
        query_text=str(query_text or "").strip(),
        knowledge_type=str(knowledge_type or "").strip() or None,
        limit=safe_limit,
        score_threshold=float(score_threshold),
    )
    return {
        "collection": service.security_knowledge_collection,
        "query_text": str(query_text or "").strip(),
        "knowledge_type": str(knowledge_type or "").strip(),
        "score_threshold": float(score_threshold),
        "count": len(items),
        "items": items,
    }


@mcp.tool(name="security_knowledge_lookup_by_id", description="按知识ID精确查询安全知识（kb_security_knowledge）")
def security_knowledge_lookup_by_id(knowledge_id: str) -> Dict[str, Any]:
    service = KnowledgeBaseService()
    target_id = str(knowledge_id or "").strip()
    if not target_id:
        return {"found": False, "message": "knowledge_id不能为空"}

    items = service.search_knowledge(
        query_text=target_id,
        collection_name=service.security_knowledge_collection,
        limit=1,
        payload_filters={"id": target_id},
        score_threshold=None,
    )
    if not items:
        return {
            "found": False,
            "knowledge_id": target_id,
            "collection": service.security_knowledge_collection,
        }

    return {
        "found": True,
        "knowledge_id": target_id,
        "collection": service.security_knowledge_collection,
        "item": items[0],
    }
