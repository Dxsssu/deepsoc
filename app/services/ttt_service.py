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
TTT_LEVEL_PHASE = "L1_phase"
TTT_LEVEL_SUB_GOAL = "L2_sub_goal"
TTT_LEVEL_ATOMIC_INTENT = "L3_atomic_intent"


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


def _safe_title(raw_title: Any, fallback: str) -> str:
    text = str(raw_title or "").strip()
    return text if text else fallback


def _stable_child_id(parent_id: str, suffix: str) -> str:
    parent = str(parent_id or uuid.uuid4())
    return f"{parent}:{suffix}"


def _collect_descendant_leaves(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    children = node.get("children") if isinstance(node.get("children"), list) else []
    if not children:
        return [node]
    result: List[Dict[str, Any]] = []
    for child in children:
        result.extend(_collect_descendant_leaves(child))
    return result


def _as_phase_title(title: str, phase_index: int) -> str:
    if title.startswith("阶段"):
        return title
    return f"阶段{phase_index}：{title}"


def _as_sub_goal_title(title: str, phase_index: int, sub_index: int) -> str:
    if title.startswith("子目标"):
        return title
    return f"子目标{phase_index}.{sub_index}：{title}"


def _as_atomic_title(title: str, phase_index: int, sub_index: int, intent_index: int) -> str:
    if title.startswith("执行意图"):
        return title
    return f"执行意图{phase_index}.{sub_index}.{intent_index}：{title}"


def _build_three_layer_from_root(root: Dict[str, Any], phase_index: int) -> Dict[str, Any]:
    root_id = str(root.get("node_id") or uuid.uuid4())
    root_status = normalize_ttt_status(root.get("status"))
    phase_title = _as_phase_title(
        _safe_title(root.get("title"), f"阶段{phase_index}"),
        phase_index,
    )
    phase_node: Dict[str, Any] = {
        "node_id": root_id,
        "title": phase_title,
        "status": root_status,
        "node_level": TTT_LEVEL_PHASE,
        "children": [],
    }

    raw_children = root.get("children") if isinstance(root.get("children"), list) else []
    l2_sources: List[Dict[str, Any]] = []
    root_as_l3 = False
    if raw_children:
        l2_sources = raw_children
    else:
        # 兼容旧的一层/两层树：自动补齐L2，并把旧节点内容下沉为L3意图。
        root_as_l3 = True
        l2_sources = [
            {
                "node_id": _stable_child_id(root_id, "l2-1"),
                "title": f"围绕“{_safe_title(root.get('title'), '当前目标')}”的假设验证",
                "status": root_status,
                "children": [],
            }
        ]

    for sub_index, l2_source in enumerate(l2_sources, start=1):
        l2_id = str(l2_source.get("node_id") or _stable_child_id(root_id, f"l2-{sub_index}"))
        l2_status = normalize_ttt_status(l2_source.get("status"))
        l2_title = _as_sub_goal_title(
            _safe_title(l2_source.get("title"), f"子目标{phase_index}.{sub_index}"),
            phase_index,
            sub_index,
        )
        l2_node: Dict[str, Any] = {
            "node_id": l2_id,
            "title": l2_title,
            "status": l2_status,
            "node_level": TTT_LEVEL_SUB_GOAL,
            "children": [],
        }

        l3_sources: List[Dict[str, Any]] = []
        if root_as_l3:
            l3_sources = [root]
        else:
            child_nodes = l2_source.get("children") if isinstance(l2_source.get("children"), list) else []
            if child_nodes:
                for child in child_nodes:
                    l3_sources.extend(_collect_descendant_leaves(child))
            else:
                # 兼容旧两层树：L2本身是叶子意图，自动下沉为L3。
                l3_sources = [l2_source]

        if not l3_sources:
            l3_sources = [
                {
                    "node_id": _stable_child_id(l2_id, "l3-1"),
                    "title": f"获取“{_safe_title(l2_source.get('title'), '该子目标')}”相关证据",
                    "status": l2_status,
                    "task_type": "query",
                    "assignee": "_operator",
                    "children": [],
                }
            ]

        for intent_index, l3_source in enumerate(l3_sources, start=1):
            l3_id = str(l3_source.get("node_id") or _stable_child_id(l2_id, f"l3-{intent_index}"))
            if l3_id in {root_id, l2_id}:
                l3_id = _stable_child_id(l2_id, f"l3-{intent_index}")
            l3_status = normalize_ttt_status(l3_source.get("status"))
            l3_title = _as_atomic_title(
                _safe_title(
                    l3_source.get("title"),
                    f"证据搜集意图{phase_index}.{sub_index}.{intent_index}",
                ),
                phase_index,
                sub_index,
                intent_index,
            )
            l3_node: Dict[str, Any] = {
                "node_id": l3_id,
                "title": l3_title,
                "status": l3_status,
                "node_level": TTT_LEVEL_ATOMIC_INTENT,
                "task_type": l3_source.get("task_type") or l2_source.get("task_type") or "query",
                "assignee": l3_source.get("assignee") or l2_source.get("assignee") or "_operator",
                "children": [],
            }
            l2_node["children"].append(l3_node)

        phase_node["children"].append(l2_node)

    return phase_node


def _coerce_three_layer_roots(roots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    coerced: List[Dict[str, Any]] = []
    for phase_index, root in enumerate(roots or [], start=1):
        coerced.append(_build_three_layer_from_root(root, phase_index))
    return coerced


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
    normalized_roots = _coerce_three_layer_roots(normalized_roots)
    normalized_roots = [_normalize_node(n) for n in normalized_roots]
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
    phase_id = "phase-1"
    roots = [
        {
            "node_id": phase_id,
            "title": "阶段一：事件处置推进",
            "status": TTT_STATUS_TODO,
            "node_level": TTT_LEVEL_PHASE,
            "children": [],
        }
    ]
    for idx, task in enumerate(tasks or [], start=1):
        sub_goal_id = f"{phase_id}:l2-{idx}"
        task_name = task.get("task_name") or f"task-{idx}"
        task_id = str(task.get("task_id") or uuid.uuid4())
        roots[0]["children"].append(
            {
                "node_id": sub_goal_id,
                "title": f"子目标1.{idx}：验证“{task_name}”",
                "status": TTT_STATUS_TODO,
                "node_level": TTT_LEVEL_SUB_GOAL,
                "children": [
                    {
                        "node_id": task_id,
                        "title": f"执行意图1.{idx}.1：{task_name}",
                        "status": TTT_STATUS_TODO,
                        "node_level": TTT_LEVEL_ATOMIC_INTENT,
                        "task_type": task.get("task_type", "query"),
                        "assignee": task.get("task_assignee", "_operator"),
                        "children": [],
                    }
                ],
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


def _iter_nodes_with_depth(tree_json: Dict[str, Any]) -> List[Tuple[Dict[str, Any], int]]:
    result: List[Tuple[Dict[str, Any], int]] = []
    roots = tree_json.get("root_nodes") if isinstance(tree_json.get("root_nodes"), list) else []

    def walk(node: Dict[str, Any], depth: int) -> None:
        result.append((node, depth))
        children = node.get("children") if isinstance(node.get("children"), list) else []
        for child in children:
            walk(child, depth + 1)

    for root in roots:
        walk(root, 1)
    return result


def _build_node_map(tree_json: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    node_map: Dict[str, Dict[str, Any]] = {}
    duplicates: List[str] = []
    for node, _ in _iter_nodes_with_depth(tree_json):
        node_id = str(node.get("node_id", "")).strip()
        if not node_id:
            continue
        if node_id in node_map:
            duplicates.append(node_id)
        node_map[node_id] = node
    return node_map, duplicates


def validate_three_layer_structure(tree_json: Dict[str, Any]) -> Dict[str, Any]:
    """校验TTT是否严格L1->L2->L3三层结构。"""
    errors: List[str] = []
    warnings: List[str] = []
    normalized = normalize_ttt_tree(copy.deepcopy(tree_json))
    roots = normalized.get("root_nodes") if isinstance(normalized.get("root_nodes"), list) else []

    if not roots:
        errors.append("TTT root_nodes 为空，无法满足三层结构。")

    node_map, duplicates = _build_node_map(normalized)
    if duplicates:
        errors.append(f"存在重复 node_id: {sorted(set(duplicates))}")

    for node, depth in _iter_nodes_with_depth(normalized):
        node_id = str(node.get("node_id", ""))
        level = str(node.get("node_level", ""))
        children = node.get("children") if isinstance(node.get("children"), list) else []

        if depth == 1:
            if level != TTT_LEVEL_PHASE:
                errors.append(f"L1节点 {node_id} 的 node_level 非 {TTT_LEVEL_PHASE}: {level}")
            if not children:
                errors.append(f"L1节点 {node_id} 缺少L2子节点。")
        elif depth == 2:
            if level != TTT_LEVEL_SUB_GOAL:
                errors.append(f"L2节点 {node_id} 的 node_level 非 {TTT_LEVEL_SUB_GOAL}: {level}")
            if not children:
                errors.append(f"L2节点 {node_id} 缺少L3子节点。")
        elif depth == 3:
            if level != TTT_LEVEL_ATOMIC_INTENT:
                errors.append(f"L3节点 {node_id} 的 node_level 非 {TTT_LEVEL_ATOMIC_INTENT}: {level}")
            if children:
                errors.append(f"L3节点 {node_id} 不应包含子节点。")
            task_type = str(node.get("task_type", "")).strip()
            assignee = str(node.get("assignee", "")).strip()
            if not task_type:
                errors.append(f"L3节点 {node_id} 缺少 task_type。")
            if not assignee:
                errors.append(f"L3节点 {node_id} 缺少 assignee。")
        else:
            errors.append(f"检测到非法深度节点（>3层）: node_id={node_id}, depth={depth}")

    stats = {
        "node_count": len(node_map),
        "root_count": len(roots),
    }
    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
        "normalized_tree": normalized,
    }


def _check_done_node_immutability(previous_tree: Dict[str, Any], candidate_tree: Dict[str, Any]) -> Dict[str, Any]:
    """校验更新是否修改了已执行的done节点。"""
    errors: List[str] = []
    warnings: List[str] = []

    previous = normalize_ttt_tree(copy.deepcopy(previous_tree))
    candidate = normalize_ttt_tree(copy.deepcopy(candidate_tree))

    old_map, _ = _build_node_map(previous)
    new_map, _ = _build_node_map(candidate)

    frozen_done_ids = [
        node_id for node_id, node in old_map.items() if str(node.get("status", "")).strip().lower() == TTT_STATUS_DONE
    ]

    for node_id in frozen_done_ids:
        old_node = old_map.get(node_id)
        new_node = new_map.get(node_id)
        if not new_node:
            errors.append(f"done节点被删除: {node_id}")
            continue

        if str(new_node.get("status", "")).strip().lower() != TTT_STATUS_DONE:
            errors.append(
                f"done节点状态被修改: {node_id}, {old_node.get('status')} -> {new_node.get('status')}"
            )

        # 冻结关键字段（避免已执行节点在后续轮次被改写语义）
        immutable_fields = ["title", "node_level", "path"]
        if str(old_node.get("node_level", "")) == TTT_LEVEL_ATOMIC_INTENT:
            immutable_fields.extend(["task_type", "assignee"])

        for field in immutable_fields:
            if str(old_node.get(field, "")) != str(new_node.get(field, "")):
                errors.append(
                    f"done节点字段被修改: node_id={node_id}, field={field}, "
                    f"old={old_node.get(field)}, new={new_node.get(field)}"
                )

    stats = {
        "frozen_done_count": len(frozen_done_ids),
    }
    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
    }


def _build_change_stats(previous_tree: Dict[str, Any], candidate_tree: Dict[str, Any]) -> Dict[str, int]:
    previous = normalize_ttt_tree(copy.deepcopy(previous_tree))
    candidate = normalize_ttt_tree(copy.deepcopy(candidate_tree))
    old_map, _ = _build_node_map(previous)
    new_map, _ = _build_node_map(candidate)

    old_ids = set(old_map.keys())
    new_ids = set(new_map.keys())
    added_ids = new_ids - old_ids
    removed_ids = old_ids - new_ids

    status_changed = 0
    meta_changed = 0
    for node_id in old_ids & new_ids:
        old_node = old_map[node_id]
        new_node = new_map[node_id]
        if str(old_node.get("status")) != str(new_node.get("status")):
            status_changed += 1
        if (
            str(old_node.get("title")) != str(new_node.get("title"))
            or str(old_node.get("node_level")) != str(new_node.get("node_level"))
            or str(old_node.get("task_type")) != str(new_node.get("task_type"))
            or str(old_node.get("assignee")) != str(new_node.get("assignee"))
        ):
            meta_changed += 1

    return {
        "added_nodes": len(added_ids),
        "removed_nodes": len(removed_ids),
        "status_changed_nodes": status_changed,
        "meta_changed_nodes": meta_changed,
    }


def validate_ttt_with_reflector(
    candidate_tree: Dict[str, Any],
    previous_tree: Optional[Dict[str, Any]] = None,
    mode: str = "update",
) -> Dict[str, Any]:
    """Reflector校验入口。

    mode:
    - init: 初始化校验（只做三层结构校验）
    - update: 更新校验（三层结构 + done节点不可修改 + 变更范围提示）
    """
    errors: List[str] = []
    warnings: List[str] = []
    safe_mode = "init" if mode == "init" else "update"

    three_layer_report = validate_three_layer_structure(candidate_tree)
    if not three_layer_report["is_valid"]:
        errors.extend(three_layer_report["errors"])
    warnings.extend(three_layer_report.get("warnings", []))

    stats: Dict[str, Any] = dict(three_layer_report.get("stats", {}))

    if safe_mode == "update" and isinstance(previous_tree, dict):
        done_report = _check_done_node_immutability(previous_tree, candidate_tree)
        if not done_report["is_valid"]:
            errors.extend(done_report["errors"])
        warnings.extend(done_report.get("warnings", []))
        stats.update(done_report.get("stats", {}))

        change_stats = _build_change_stats(previous_tree, candidate_tree)
        stats.update(change_stats)

        # “谨慎更新”提示：大幅结构变化会给出警告（不直接阻断）
        if change_stats.get("added_nodes", 0) + change_stats.get("removed_nodes", 0) > 6:
            warnings.append(
                "本轮结构变化较大（新增+删除节点数 > 6），请确认是否符合最小改动原则。"
            )

    return {
        "mode": safe_mode,
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
        "normalized_tree": three_layer_report.get("normalized_tree"),
    }
