# DeepSOC 知识库设计与实现

## 1. Qdrant 向量数据库介绍

Qdrant 是面向向量检索的数据库，适合将非结构化安全知识（SOP、案例、术语解释等）转换为向量后进行相似度检索。在 DeepSOC 中，Qdrant 主要承担以下职责：

- 存储知识分块后的向量数据（point）
- 按相似度检索与当前告警最相关的知识片段
- 结合 payload 过滤条件（如 `tenant_id`、`status`）做租户级隔离和有效性控制

当前项目采用「SQL 元数据 + Qdrant 向量」双存储模式：

- SQL 表 `kb_documents` 保存文档级元信息（`doc_id`、`kb_type`、`title`、`tags`、`status`、`version` 等）
- Qdrant 保存 chunk 级向量和 payload（`kb_type`、`doc_id`、`chunk_index`、`title`、`text`、`tags` 等）

核心实现位置：

- 服务层：[knowledge_base_service.py](/Users/sssu/Project/deepsoc/app/services/knowledge_base_service.py)
- MCP 检索服务：[kb_search_server.py](/Users/sssu/Project/deepsoc/app/utils/mcp_servers/kb_search_server.py)
- 初始化脚本：[init_knowledge_base.py](/Users/sssu/Project/deepsoc/tools/init_knowledge_base.py)

默认运行方式（Docker）：

```bash
docker run -p 6333:6333 -p 6334:6334 \
  -v $(pwd)/sql_data/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

## 2. 知识库结构设计

### 2.1 总体设计

知识库按类型拆分为 5 个逻辑域，并映射为 5 个 Qdrant Collection（每类一库）：

- `kb_playbook`：存 SOP、研判流程、调查步骤等
- `kb_context`：存资产、网络拓扑、业务知识、账号关系、白名单、基线
- `kb_cases`：存历史事件、复盘、误报案例、处置经验、专家总结
- `kb_security_knowledge`：存术语定义、机制原理、标准映射、检测解释、判定边界、通用安全知识
- `kb_policy_personal`：存租户偏好、内部口径、例外流程、个性化规则

数据目录结构：

- [data/knowledge_base/kb_playbook](/Users/sssu/Project/deepsoc/data/knowledge_base/kb_playbook)
- [data/knowledge_base/kb_context](/Users/sssu/Project/deepsoc/data/knowledge_base/kb_context)
- [data/knowledge_base/kb_cases](/Users/sssu/Project/deepsoc/data/knowledge_base/kb_cases)
- [data/knowledge_base/kb_security_knowledge](/Users/sssu/Project/deepsoc/data/knowledge_base/kb_security_knowledge)
- [data/knowledge_base/kb_policy_personal](/Users/sssu/Project/deepsoc/data/knowledge_base/kb_policy_personal)

### 2.2 文档模型与分块索引

文档建议字段：

- `doc_id`：文档唯一标识
- `kb_type`：知识类型（上述五类之一）
- `title`：标题
- `content`：正文
- `tags`：标签
- `metadata`：扩展元信息
- `status`：`draft/published/deprecated`

入库流程：

1. 脚本加载 JSON 文档（支持按目录自动推断 `kb_type`）
2. 写入 SQL 文档元数据
3. 对 `content` 做分块（`KB_CHUNK_SIZE` + `KB_CHUNK_OVERLAP`）
4. 生成向量并写入对应 Collection

关键配置（`.env`）：

- `KB_COLLECTION_PREFIX` 与 `KB_COLLECTION_KB_*`：Collection 命名
- `KB_TOP_K`：默认召回数量
- `KB_SCORE_THRESHOLD`：全局检索阈值
- `KB_CASES_HIT_SCORE_THRESHOLD`：Captain 首轮判定 `kb_cases` 命中阈值（独立阈值）

### 2.3 检索策略（当前实现）

检索接口有两类：

- `kb_search`：按指定 `kb_types` 检索
- `kb_search_for_role`：按角色默认知识域检索

Captain 首轮采用分阶段策略：

1. 先检索 `kb_security_knowledge + kb_context` 做初始研判
2. 再检索 `kb_cases`（受 `KB_CASES_HIT_SCORE_THRESHOLD` 控制）
3. 若 `kb_cases` 未命中，再检索 `kb_playbook`

这样可以降低“案例库过度命中”带来的路径偏置。

## 3. 与各个 Agent 角色融合

### 3.1 Captain（总指挥）

Captain 在首轮会自动执行知识检索并将结果注入提示词，不依赖人工触发：

- 入口：[captain_service.py](/Users/sssu/Project/deepsoc/app/services/captain_service.py)
- 逻辑：[knowledge_base_service.py](/Users/sssu/Project/deepsoc/app/services/knowledge_base_service.py)

融合效果：

- 首轮优先获得“原理 + 上下文 + 历史经验/SOP”
- 通过 `kb_cases` 阈值避免低相关历史误导决策
- 任务下发前已具备基础知识支撑，减少盲查

### 3.2 Manager（安全管理员）

Manager 通过 MCP 工具体系可调用 `knowledge_base_mcp` 做检索，将 Captain 任务细化为可执行动作。典型用法：

- 先查流程与上下文（`kb_playbook`、`kb_context`）
- 再把动作翻译给 Operator（查询、处置、通知）

### 3.3 Operator（一线操作员）

Operator 执行动作时可结合知识库查询结果调整执行细节与证据采集顺序。典型场景：

- 执行前查 SOP 步骤与判定边界
- 执行后回填事实，形成后续轮次可复用上下文

### 3.4 Expert（安全专家）

Expert 侧重点是复盘与建议，可结合 `kb_cases` 与 `kb_security_knowledge`：

- 比对当前处置路径与历史最佳实践
- 输出“遗漏点、风险点、改进建议”

### 3.5 角色默认知识域（角色检索）

系统定义了角色默认检索范围（用于 `kb_search_for_role`）：

- `_captain`：`kb_policy_personal` + `kb_context` + `kb_cases`
- `_manager`：`kb_policy_personal` + `kb_playbook` + `kb_context`
- `_operator`：`kb_policy_personal` + `kb_playbook` + `kb_context`
- `_expert`：`kb_policy_personal` + `kb_cases` + `kb_security_knowledge` + `kb_context`

说明：Captain 首轮有独立的分阶段检索逻辑，优先级高于通用角色默认检索。

## 附：推荐运维动作

- 新增或修改知识后，执行：

```bash
python3 tools/init_knowledge_base.py
```

- 若调整命中灵敏度，优先调参：

```bash
KB_CASES_HIT_SCORE_THRESHOLD=0.65
KB_SCORE_THRESHOLD=
KB_TOP_K=8
```
