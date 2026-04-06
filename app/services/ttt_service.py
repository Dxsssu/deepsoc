import copy
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.models import db, TracebackTaskTree

logger = logging.getLogger(__name__)

TTT_SCHEMA_VERSION = "1.0"
TTT_STATUS_TODO = "todo"
TTT_STATUS_IN_PROGRESS = "in_progress"
TTT_STATUS_DONE = "done"
TTT_STATUS_NA = "n/a"
FINAL_TTT_STATUSES = {TTT_STATUS_DONE, TTT_STATUS_NA}


def normalize_ttt_status(raw_status: Any) -> str:
    value = str(raw_status or "").strip().lower()
    if value in {"todo", "to-do", "to_do", "pending"}:
        return TTT_STATUS_TODO
    if value in {"in_progress", "in-progress", "processing", "doing"}:
        return TTT_STATUS_IN_PROGRESS
    if value in {"done", "completed", "complete", "resolved"}:
        return TTT_STATUS_DONE
    if value in {"n/a", "na", "not_applicable", "not-applicable", "blocked", "failed", "error"}:
        return TTT_STATUS_NA
    return TTT_STATUS_TODO


def _normalize_node(node: Dict[str, Any], parent_path: str = "") -> Dict[str, Any]:
    normalized = copy.deepcopy(node if isinstance(node, dict) else {})
    node_id = normalized.get("node_id") or normalized.get("id") or str(uuid.uuid4())
    title = normalized.get("title") or normalized.get("task_name") or normalized.get("name") or f"node-{node_id[:8]}"
    status = normalize_ttt_status(normalized.get("status"))

    path = f"{parent_path}/{title}" if parent_path else title
    children = normalized.get("children")
    if not isinstance(children, list):
        children = []

    normalized_children = [_normalize_node(c, path) for c in children]
    normalized["node_id"] = str(node_id)
    normalized["title"] = str(title)
    normalized["status"] = status
    normalized["path"] = path
    normalized["children"] = normalized_children
    # 新版TTT不保留priority字段，优先级由Manager+LLM动态判断
    normalized.pop("priority", None)
    return normalized


def _collect_status_map(node: Dict[str, Any], status_map: Dict[str, str]) -> None:
    node_id = node.get("node_id")
    if node_id:
        status_map[str(node_id)] = normalize_ttt_status(node.get("status"))
    for child in (node.get("children") if isinstance(node.get("children"), list) else []):
        _collect_status_map(child, status_map)


def _apply_status_map(node: Dict[str, Any], status_map: Dict[str, str]) -> None:
    node_id = node.get("node_id")
    if node_id and str(node_id) in status_map:
        node["status"] = status_map[str(node_id)]
    for child in (node.get("children") if isinstance(node.get("children"), list) else []):
        _apply_status_map(child, status_map)


def normalize_ttt_tree(tree_json: Optional[Dict[str, Any]], event_id: Optional[str] = None, round_id: Optional[int] = None) -> Dict[str, Any]:
    tree = copy.deepcopy(tree_json if isinstance(tree_json, dict) else {})
    roots = tree.get("root_nodes")
    if not isinstance(roots, list):
        roots = tree.get("nodes")
    if not isinstance(roots, list):
        roots = []

    normalized_roots = [_normalize_node(n) for n in roots]
    normalized = {
        "schema_version": str(tree.get("schema_version") or TTT_SCHEMA_VERSION),
        "event_id": event_id or tree.get("event_id"),
        "round_id": round_id if round_id is not None else tree.get("round_id"),
        "root_nodes": normalized_roots,
    }
    return normalized


def apply_status_updates_from_candidate(base_tree_json: Dict[str, Any], candidate_tree_json: Dict[str, Any], event_id: Optional[str] = None, round_id: Optional[int] = None) -> Dict[str, Any]:
    """
    基于base树结构，仅吸收candidate中的节点状态。
    - 保持base的节点结构（node_id/title/层级）不变
    - 只更新status
    """
    base = normalize_ttt_tree(base_tree_json, event_id=event_id, round_id=round_id)
    candidate = normalize_ttt_tree(candidate_tree_json, event_id=event_id, round_id=round_id)

    status_map: Dict[str, str] = {}
    for root in candidate.get("root_nodes", []):
        _collect_status_map(root, status_map)

    for root in base.get("root_nodes", []):
        _apply_status_map(root, status_map)
    return base


def build_ttt_from_task_list(event_id: str, round_id: int, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    roots = []
    for idx, task in enumerate(tasks or []):
        roots.append(
            {
                "node_id": str(task.get("task_id") or uuid.uuid4()),
                "title": task.get("task_name") or f"task-{idx + 1}",
                "status": TTT_STATUS_TODO,
                "task_type": task.get("task_type", "query"),
                "assignee": task.get("task_assignee", "_operator"),
                "children": [],
            }
        )

    return normalize_ttt_tree(
        {
            "schema_version": TTT_SCHEMA_VERSION,
            "event_id": event_id,
            "round_id": round_id,
            "root_nodes": roots,
        },
        event_id=event_id,
        round_id=round_id,
    )


def get_latest_ttt_snapshot(event_id: str, with_for_update: bool = False) -> Optional[TracebackTaskTree]:
    query = TracebackTaskTree.query.filter_by(event_id=event_id).order_by(TracebackTaskTree.ttt_version.desc())
    if with_for_update:
        query = query.with_for_update()
    return query.first()


def save_ttt_snapshot(
    event_id: str,
    tree_json: Dict[str, Any],
    updated_by: str = "_captain",
    schema_version: str = TTT_SCHEMA_VERSION,
    auto_commit: bool = False,
) -> TracebackTaskTree:
    normalized_tree = normalize_ttt_tree(tree_json, event_id=event_id)
    latest = get_latest_ttt_snapshot(event_id, with_for_update=True)
    next_version = 1 if not latest else int(latest.ttt_version) + 1

    snapshot = TracebackTaskTree(
        event_id=event_id,
        ttt_version=next_version,
        ttt_schema_version=schema_version or TTT_SCHEMA_VERSION,
        tree_json=normalized_tree,
        updated_by=updated_by or "_captain",
    )
    db.session.add(snapshot)
    db.session.flush()
    if auto_commit:
        db.session.commit()
    logger.info(f"TTT snapshot saved: event={event_id}, version={next_version}, updated_by={updated_by}")
    return snapshot


def _collect_leaf_nodes(node: Dict[str, Any], expected_status: Optional[str], result: List[Dict[str, Any]]) -> None:
    children = node.get("children") if isinstance(node.get("children"), list) else []
    if children:
        for child in children:
            _collect_leaf_nodes(child, expected_status, result)
        return

    node_status = normalize_ttt_status(node.get("status"))
    if expected_status and node_status != expected_status:
        return
    result.append(
        {
            "node_id": node.get("node_id"),
            "title": node.get("title"),
            "status": node_status,
            "task_type": node.get("task_type", "query"),
            "assignee": node.get("assignee", "_operator"),
            "path": node.get("path", ""),
            "raw_node": node,
        }
    )


def list_leaf_nodes_by_status(tree_json: Dict[str, Any], status: Optional[str] = None) -> List[Dict[str, Any]]:
    normalized = normalize_ttt_tree(tree_json)
    expected = normalize_ttt_status(status) if status else None
    leaves: List[Dict[str, Any]] = []
    for node in normalized.get("root_nodes", []):
        _collect_leaf_nodes(node, expected, leaves)
    return leaves


def select_next_todo_leaf(tree_json: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    todo_leaves = list_leaf_nodes_by_status(tree_json, TTT_STATUS_TODO)
    if not todo_leaves:
        return None
    # 兜底场景按路径稳定排序，常规优先级由Manager调用LLM判断
    todo_leaves.sort(key=lambda x: (x.get("path", ""), x.get("node_id", "")))
    return todo_leaves[0]


def _update_node_status(node: Dict[str, Any], node_id: str, new_status: str, extra_fields: Optional[Dict[str, Any]]) -> bool:
    if str(node.get("node_id")) == str(node_id):
        node["status"] = normalize_ttt_status(new_status)
        if isinstance(extra_fields, dict):
            node.update(extra_fields)
        return True

    children = node.get("children") if isinstance(node.get("children"), list) else []
    for child in children:
        if _update_node_status(child, node_id, new_status, extra_fields):
            return True
    return False


def set_node_status(tree_json: Dict[str, Any], node_id: str, new_status: str, extra_fields: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], bool]:
    normalized = normalize_ttt_tree(tree_json)
    updated = False
    for root in normalized.get("root_nodes", []):
        if _update_node_status(root, node_id, new_status, extra_fields):
            updated = True
            break
    return normalized, updated


def _find_node_by_id(node: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    if str(node.get("node_id")) == str(node_id):
        return node
    for child in (node.get("children") if isinstance(node.get("children"), list) else []):
        found = _find_node_by_id(child, node_id)
        if found:
            return found
    return None


def find_node_by_id(tree_json: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_ttt_tree(tree_json)
    for root in normalized.get("root_nodes", []):
        found = _find_node_by_id(root, node_id)
        if found:
            return found
    return None


def count_nodes_by_status(tree_json: Dict[str, Any]) -> Dict[str, int]:
    counts = {
        TTT_STATUS_TODO: 0,
        TTT_STATUS_IN_PROGRESS: 0,
        TTT_STATUS_DONE: 0,
        TTT_STATUS_NA: 0,
    }
    for status in counts.keys():
        counts[status] = len(list_leaf_nodes_by_status(tree_json, status))
    return counts


def has_open_work(tree_json: Dict[str, Any]) -> bool:
    counts = count_nodes_by_status(tree_json)
    return counts[TTT_STATUS_TODO] > 0 or counts[TTT_STATUS_IN_PROGRESS] > 0
