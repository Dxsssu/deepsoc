#!/usr/bin/env python3
"""TTT 更新机制演示脚本（当前逻辑：无结构锁）。

目标：
1. 预置一棵三层 TTT（L1->L2->L3）
2. 模拟 Manager 抽取 todo 叶子并认领
3. 模拟 Captain 在下一轮读取：
   - 最新 TTT 快照
   - 上轮 summary
   - 历史任务
   然后输出可“谨慎改结构”的新 TTT 快照
4. 展示“当前逻辑（允许结构更新）”每一步变化
5. 可选对比“旧逻辑（结构锁，仅状态同步）”会丢失哪些结构更新

使用示例：
  .\\.venv\\Scripts\\python.exe tools/test_ttt_update_mechanism.py
  .\\.venv\\Scripts\\python.exe tools/test_ttt_update_mechanism.py --no-compare-legacy-lock
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from typing import Dict, Any, List, Tuple


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.services.ttt_service import (
    TTT_STATUS_TODO,
    TTT_STATUS_IN_PROGRESS,
    TTT_STATUS_DONE,
    TTT_STATUS_NA,
    apply_status_updates_from_candidate,
    count_nodes_by_status,
    list_leaf_nodes_by_status,
    normalize_ttt_tree,
    select_next_todo_leaf,
    set_node_status,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="测试 TTT 树更新机制（含每步输出）")
    parser.add_argument(
        "--event-id",
        default="demo-ttt-update-event",
        help="演示用 event_id（默认: demo-ttt-update-event）",
    )
    parser.add_argument(
        "--compare-legacy-lock",
        action="store_true",
        default=True,
        help="对比旧结构锁行为（默认开启）",
    )
    parser.add_argument(
        "--no-compare-legacy-lock",
        action="store_false",
        dest="compare_legacy_lock",
        help="不对比旧结构锁行为",
    )
    return parser


def build_initial_ttt(event_id: str) -> Dict[str, Any]:
    """预设初始 TTT（严格三层）。"""
    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "round_id": 1,
        "root_nodes": [
            {
                "node_id": "phase-1",
                "title": "阶段一：攻击源定性",
                "status": TTT_STATUS_TODO,
                "node_level": "L1_phase",
                "children": [
                    {
                        "node_id": "phase-1:l2-1",
                        "title": "子目标1.1：确认源IP是否为已知恶意基础设施",
                        "status": TTT_STATUS_TODO,
                        "node_level": "L2_sub_goal",
                        "children": [
                            {
                                "node_id": "phase-1:l2-1:l3-1",
                                "title": "执行意图1.1.1：获取该IP外部威胁情报标签及信誉评分",
                                "status": TTT_STATUS_TODO,
                                "node_level": "L3_atomic_intent",
                                "task_type": "query",
                                "assignee": "_operator",
                                "children": [],
                            }
                        ],
                    },
                    {
                        "node_id": "phase-1:l2-2",
                        "title": "子目标1.2：确认源IP针对我方历史是否有恶意试探",
                        "status": TTT_STATUS_TODO,
                        "node_level": "L2_sub_goal",
                        "children": [
                            {
                                "node_id": "phase-1:l2-2:l3-1",
                                "title": "执行意图1.2.1：检索近30天该IP触发的内网历史告警",
                                "status": TTT_STATUS_TODO,
                                "node_level": "L3_atomic_intent",
                                "task_type": "query",
                                "assignee": "_operator",
                                "children": [],
                            },
                            {
                                "node_id": "phase-1:l2-2:l3-2",
                                "title": "执行意图1.2.2：查询边界防火墙对该IP的历史拦截记录",
                                "status": TTT_STATUS_TODO,
                                "node_level": "L3_atomic_intent",
                                "task_type": "query",
                                "assignee": "_operator",
                                "children": [],
                            },
                        ],
                    },
                ],
            }
        ],
    }


def build_captain_candidate_round2(event_id: str) -> Dict[str, Any]:
    """模拟 Captain R2 候选输出：最小幅度变更 + 必要结构扩展。"""
    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "round_id": 2,
        "root_nodes": [
            {
                "node_id": "phase-1",
                "title": "阶段一：攻击源定性",
                "status": TTT_STATUS_IN_PROGRESS,
                "node_level": "L1_phase",
                "children": [
                    {
                        "node_id": "phase-1:l2-1",
                        "title": "子目标1.1：确认源IP是否为已知恶意基础设施",
                        "status": TTT_STATUS_DONE,
                        "node_level": "L2_sub_goal",
                        "children": [
                            {
                                "node_id": "phase-1:l2-1:l3-1",
                                "title": "执行意图1.1.1：获取该IP外部威胁情报标签及信誉评分",
                                "status": TTT_STATUS_DONE,
                                "node_level": "L3_atomic_intent",
                                "task_type": "query",
                                "assignee": "_operator",
                                "children": [],
                            }
                        ],
                    },
                    {
                        "node_id": "phase-1:l2-2",
                        "title": "子目标1.2：确认源IP针对我方历史是否有恶意试探",
                        "status": TTT_STATUS_IN_PROGRESS,
                        "node_level": "L2_sub_goal",
                        "children": [
                            {
                                "node_id": "phase-1:l2-2:l3-1",
                                "title": "执行意图1.2.1：检索近30天该IP触发的内网历史告警",
                                "status": TTT_STATUS_DONE,
                                "node_level": "L3_atomic_intent",
                                "task_type": "query",
                                "assignee": "_operator",
                                "children": [],
                            },
                            {
                                "node_id": "phase-1:l2-2:l3-2",
                                "title": "执行意图1.2.2：查询边界防火墙对该IP的历史拦截记录",
                                "status": TTT_STATUS_TODO,
                                "node_level": "L3_atomic_intent",
                                "task_type": "query",
                                "assignee": "_operator",
                                "children": [],
                            },
                            {
                                "node_id": "phase-1:l2-2:l3-3",
                                "title": "执行意图1.2.3：关联同源IP近7天攻击资产分布",
                                "status": TTT_STATUS_TODO,
                                "node_level": "L3_atomic_intent",
                                "task_type": "query",
                                "assignee": "_operator",
                                "children": [],
                            },
                        ],
                    },
                ],
            },
            {
                "node_id": "phase-2",
                "title": "阶段二：边界突破排查",
                "status": TTT_STATUS_TODO,
                "node_level": "L1_phase",
                "children": [
                    {
                        "node_id": "phase-2:l2-1",
                        "title": "子目标2.1：确认告警后是否存在成功登录",
                        "status": TTT_STATUS_TODO,
                        "node_level": "L2_sub_goal",
                        "children": [
                            {
                                "node_id": "phase-2:l2-1:l3-1",
                                "title": "执行意图2.1.1：查询告警后30分钟目标系统认证成功日志",
                                "status": TTT_STATUS_TODO,
                                "node_level": "L3_atomic_intent",
                                "task_type": "query",
                                "assignee": "_operator",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        ],
    }


def print_title(text: str) -> None:
    print("\n" + "=" * 88)
    print(text)
    print("=" * 88)


def print_tree(node: Dict[str, Any], indent: int = 0) -> None:
    pad = "  " * indent
    level = node.get("node_level", "")
    status = node.get("status", "")
    node_id = node.get("node_id", "")
    task_type = node.get("task_type")
    tail = f", task_type={task_type}" if task_type else ""
    print(f"{pad}- [{level}] {node.get('title')} (id={node_id}, status={status}{tail})")
    for child in node.get("children", []):
        print_tree(child, indent + 1)


def print_snapshot(version: int, tree_json: Dict[str, Any], note: str = "") -> None:
    print_title(f"TTT 快照 v{version} {note}")
    counts = count_nodes_by_status(tree_json)
    print(f"event_id={tree_json.get('event_id')}, round_id={tree_json.get('round_id')}, schema={tree_json.get('schema_version')}")
    print(f"状态统计: todo={counts.get(TTT_STATUS_TODO, 0)}, in_progress={counts.get(TTT_STATUS_IN_PROGRESS, 0)}, done={counts.get(TTT_STATUS_DONE, 0)}, n/a={counts.get(TTT_STATUS_NA, 0)}")
    for root in tree_json.get("root_nodes", []):
        print_tree(root, 0)
    print("\n当前 todo 叶子:")
    for leaf in list_leaf_nodes_by_status(tree_json, TTT_STATUS_TODO):
        print(f"  - {leaf.get('node_id')} | {leaf.get('path')} | {leaf.get('title')}")


def _collect_node_map(tree_json: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}

    def walk(node: Dict[str, Any]) -> None:
        node_id = str(node.get("node_id", ""))
        if node_id:
            result[node_id] = node
        for child in node.get("children", []):
            walk(child)

    for root in tree_json.get("root_nodes", []):
        walk(root)
    return result


def summarize_diff(old_tree: Dict[str, Any], new_tree: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    old_map = _collect_node_map(old_tree)
    new_map = _collect_node_map(new_tree)
    old_ids = set(old_map.keys())
    new_ids = set(new_map.keys())

    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    status_changed: List[str] = []
    for node_id in sorted(old_ids & new_ids):
        old_status = old_map[node_id].get("status")
        new_status = new_map[node_id].get("status")
        if old_status != new_status:
            status_changed.append(f"{node_id}: {old_status} -> {new_status}")
    return added, removed, status_changed


def main() -> int:
    args = build_parser().parse_args()
    event_id = args.event_id

    print_title("Step 0 - 初始化 TTT（预设树）")
    current_tree = normalize_ttt_tree(build_initial_ttt(event_id), event_id=event_id, round_id=1)
    ttt_version = 1
    print_snapshot(ttt_version, current_tree, "(初始化)")

    # Step 1: Manager 抽取一个 todo 节点并认领
    print_title("Step 1 - Manager 抽取 todo 叶子并置为 in_progress")
    selected_leaf = select_next_todo_leaf(current_tree)
    if not selected_leaf:
        print("未找到可执行的 todo 叶子节点，流程中止。")
        return 1
    print(f"Manager 选中节点: {selected_leaf.get('node_id')} | {selected_leaf.get('title')}")
    current_tree, updated = set_node_status(
        current_tree,
        selected_leaf.get("node_id"),
        TTT_STATUS_IN_PROGRESS,
        extra_fields={"claimed_by": "_manager", "claimed_round_id": 1},
    )
    if not updated:
        print("节点状态更新失败，流程中止。")
        return 1
    ttt_version += 1
    print_snapshot(ttt_version, current_tree, "(Manager 认领后)")

    # 构造“Captain后续轮次输入上下文”
    last_round_summary = (
        "上一轮结论：该IP威胁情报评分较高，已确认具备恶意基础设施特征；"
        "但仍需补充历史试探范围与是否已出现边界突破迹象。"
    )
    history_tasks = [
        {
            "task_id": "task-r1-001",
            "task_name": "获取该IP外部威胁情报标签及信誉评分",
            "task_status": "completed",
            "round_id": 1,
        }
    ]

    print_title("Step 2 - Captain 后续轮次更新（当前逻辑：允许结构调整）")
    print("Captain 读取输入：latest_ttt + last_round_summary + history_tasks")
    print(f"last_round_summary: {last_round_summary}")
    print(f"history_tasks: {json.dumps(history_tasks, ensure_ascii=False)}")

    captain_candidate = build_captain_candidate_round2(event_id)
    # 当前逻辑：不再结构锁，候选树规范化后直接保存为新快照
    new_tree_current_logic = normalize_ttt_tree(captain_candidate, event_id=event_id, round_id=2)
    added, removed, status_changed = summarize_diff(current_tree, new_tree_current_logic)
    print("当前逻辑差异摘要：")
    print(f"  新增节点数: {len(added)}")
    print(f"  删除节点数: {len(removed)}")
    print(f"  状态变化数: {len(status_changed)}")
    if added:
        print("  新增节点ID:", ", ".join(added))
    if status_changed:
        print("  部分状态变化:")
        for item in status_changed[:6]:
            print("   -", item)

    ttt_version += 1
    current_tree = copy.deepcopy(new_tree_current_logic)
    print_snapshot(ttt_version, current_tree, "(Captain R2 更新后)")

    if args.compare_legacy_lock:
        print_title("附加对比 - 若沿用旧结构锁（仅状态同步）会怎样")
        legacy_locked_tree = apply_status_updates_from_candidate(
            base_tree_json=normalize_ttt_tree(build_initial_ttt(event_id), event_id=event_id, round_id=1),
            candidate_tree_json=new_tree_current_logic,
            event_id=event_id,
            round_id=2,
        )
        l_added, l_removed, l_status_changed = summarize_diff(
            normalize_ttt_tree(build_initial_ttt(event_id), event_id=event_id, round_id=1),
            legacy_locked_tree,
        )
        print("旧逻辑差异摘要（相对初始树）：")
        print(f"  新增节点数: {len(l_added)}  (通常应为0)")
        print(f"  删除节点数: {len(l_removed)}")
        print(f"  状态变化数: {len(l_status_changed)}")
        if l_added:
            print("  新增节点ID:", ", ".join(l_added))
        else:
            print("  说明：新增分支/新增任务意图会被结构锁抹平，只保留状态变化。")

    # Step 3: Manager 基于新树继续抽取下一任务
    print_title("Step 3 - Manager 在新结构上继续抽取 todo 叶子")
    next_leaf = select_next_todo_leaf(current_tree)
    if not next_leaf:
        print("未找到 todo 叶子节点，流程结束。")
        return 0
    print(f"Manager 新选中节点: {next_leaf.get('node_id')} | {next_leaf.get('title')}")
    current_tree, updated = set_node_status(
        current_tree,
        next_leaf.get("node_id"),
        TTT_STATUS_IN_PROGRESS,
        extra_fields={"claimed_by": "_manager", "claimed_round_id": 2},
    )
    if not updated:
        print("Step 3 更新失败。")
        return 1
    ttt_version += 1
    print_snapshot(ttt_version, current_tree, "(Manager R2 认领后)")

    # Step 4: 模拟执行失败/不可用，置 n/a
    print_title("Step 4 - 模拟执行异常，节点置为 n/a")
    current_tree, updated = set_node_status(
        current_tree,
        next_leaf.get("node_id"),
        TTT_STATUS_NA,
        extra_fields={"result_note": "数据源缺失，当前轮次不可判定"},
    )
    if not updated:
        print("Step 4 更新失败。")
        return 1
    ttt_version += 1
    print_snapshot(ttt_version, current_tree, "(异常回写后)")

    print_title("演示结束")
    print("已演示当前机制下的逐步更新：初始化 -> Manager认领 -> Captain结构更新 -> Manager继续执行 -> 异常回写。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

