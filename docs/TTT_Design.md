# TTT 设计说明（后端第一版）

## 1. 目标
- 将 Captain 从“直接下发任务”升级为“全局规划师”。
- 引入 TTT（Traceback Task Tree）作为共享黑板。
- Manager 每轮仅从 TTT 中抽取一个 `todo` 叶子节点执行。

## 2. 数据结构
- 新增表：`traceback_task_trees`
- 关键字段：
  - `event_id`
  - `ttt_version`（同一事件递增版本）
  - `ttt_schema_version`
  - `tree_json`（完整 TTT 快照）
  - `updated_by`

TTT JSON 约定（v1）：
- 顶层字段：`schema_version`, `event_id`, `round_id`, `root_nodes`
- 节点字段（最小）：`node_id`, `title`, `status`, `children`
- 节点层级字段：`node_level`
- 叶子字段（必填）：`task_type`, `assignee`

TTT 采用严格三层结构：
- L1（战略层 / Phase）：`node_level = L1_phase`
- L2（战术层 / Sub-Goal）：`node_level = L2_sub_goal`
- L3（执行层 / Atomic Intent）：`node_level = L3_atomic_intent`

结构约束：
- 只允许 `L1 -> L2 -> L3`
- L3 必须是叶子节点（`children: []`）
- L1/L2 不得承载可执行动作

状态枚举（叶子节点）：
- `todo`
- `in_progress`
- `done`
- `n/a`

## 3. 流程
1. Captain 读取最新 TTT（无则初始化）。
2. Captain 输出完整 TTT 快照并保存新版本。
3. Manager 在 `event_status=processing` 时读取最新 TTT。
4. Manager 每轮最多抽取 1 个 `todo` 叶子节点，先标记为 `in_progress`，再创建 1 条 Task。
5. Task 继续走原有 Action/Command/Execution/Summary 链路。
6. 下一轮 Captain 结合 summary 再更新 TTT。
   - 后续轮次允许在必要时调整TTT结构与节点任务信息（不仅是状态更新）。
   - 调整策略遵循“最小改动优先”，仅在出现新证据/新假设或原结构无法表达时再改结构。
7. Reflector 校验：
   - 初始化后：必须通过“三层结构校验”（L1->L2->L3）。
   - 更新后：必须通过“三层结构校验”与“done节点冻结校验”。
   - done节点冻结规则：已执行完成（status=done）的节点不得被删除、不得降级状态、不得修改标题/层级/任务信息。
   - 更新校验失败时：更新轮次回退到上一版快照；初始化轮次校验失败则中止并标记事件错误。

## 4. 兼容策略
- 若 Captain 仍返回旧协议 `TASK`，后端会自动转换为一层 TTT。
- 旧的 `pending task` 消费逻辑在 Manager 中保留兜底，确保平滑迁移。

## 5. 后续演进建议
- 可拆分 `ttt_service` 为 `store / selector / updater` 三个模块。
- 可增加 `event/<id>/ttt` 查询接口供前端独立渲染黑板。
- 可引入节点级并发锁与冲突检测（基于 `ttt_version`）。
