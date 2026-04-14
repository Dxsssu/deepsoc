# 知识库架构更新说明（2026-04）

## 1. 背景与目标

本次知识库升级的核心目标是将“结构化、可精确命中”的知识与“语义化、经验型”的知识分层管理，避免单一存储形态带来的复杂度和误检问题。  
整体原则是：

1. 已知查询键的知识放在 SQLite，用精确检索保证稳定性与可控性。  
2. 不确定查询键、依赖语义相似的知识放在 Qdrant，用向量检索提升召回能力。  
3. 运行时由 Captain 和 MCP 工具按场景调用，不强行统一为一种检索方式。

## 2. 当前总体架构

当前知识库采用“双引擎分层架构”：

1. SQLite 层：存放可精确命中的结构化知识，包括 SOP、资产信息和实时标注。  
2. Qdrant 层：存放需要语义理解的向量知识，包括历史案例与安全知识。  

这种分层方式让系统在查询时可以“按问题选引擎”：  
如果问题是“某个实体是什么”，优先走结构化精确查询；  
如果问题是“是否存在相似经验/相关知识”，优先走语义检索。

## 3. SQLite 知识层

### 3.1 SOP（sop）

SOP 的定位是“初始化阶段的调查骨架”，强调泛化流程，不写死执行工具。  
当前关键字段如下：

1. `alert_type_key`：告警类型索引键（唯一）。  
2. `title`：SOP 标题。  
3. `content_md`：Markdown 流程正文。  
4. `version`、`is_active`：版本与启用状态。  

这保证了 SOP 既可被程序消费，也便于人工维护与版本化管理。

### 3.2 资产库（assets）

资产库用于回答“这个实体是什么、归谁、重要性如何”。  
为兼容更多实体类型，采用统一索引字段：

1. `asset_key`：统一主键（可放 IP、主机名、域名等）。  
2. `asset_type`：资产类型标记（可选）。  
3. `asset_group`、`criticality`、`owner`：治理字段。  
4. `metadata_json`：扩展字段，承载业务属性、标签等。  

这种设计可以避免后续新增实体类型时反复改表。

### 3.3 实时标注（runtime_annotations）

实时标注用于记录短期运营语义。  
当前按“简化内容模型”存储：

1. `annotation_key`：标注对象键（如 `ip:1.2.3.4`、`account:xxx`）。  
2. `title`：标注标题。  
3. `content_md`：标注内容。  

该表主要服务于告警解释、策略例外说明和运营协同。

## 4. Qdrant 知识层

### 4.1 历史案例库（kb_cases）

历史案例库已按简化结构重构，payload 字段为：

1. `id`  
2. `alert_type`  
3. `severity`  
4. `alert_payload`  
5. `actions`（可存最终 TTT 快照或其摘要）  
6. `closed_at`  

说明：Qdrant 的 `point.id` 仅接受整数或 UUID。  
系统已实现兼容策略：如果业务 `id` 不是整数/UUID，会稳定映射为 UUID 写入 Qdrant，同时保留原始 `payload.id` 供业务检索与展示。

### 4.2 安全知识库（kb_security_knowledge）

安全知识库也已简化，payload 字段为：

1. `id`  
2. `title`  
3. `content`  
4. `knowledge_type`（限定为 `attack_technique/cve/term/tool`）  

该库用于术语解释、攻击技术补充和漏洞背景参考，降低 Agent 在陌生领域的幻觉风险。

## 5. 运行时接入链路

### 5.1 Captain 初始化轮次（已切换）

SOP 接入逻辑已从“语义检索”切换为“目录路由”：

1. Captain 从 SQLite 读取激活的 SOP 列表。  
2. 将“当前告警输入 + SOP 列表”共同提交给 LLM。  
3. LLM 输出：
   - `selected_sop`（选择了哪条 SOP）  
   - `referenced_flow_md`（用于初始化 TTT 的参考流程）  
4. Captain 使用 `referenced_flow_md` 初始化 TTT 骨架。  

这使 SOP 的选择路径更可解释，也更贴合“标准流程模板”的定位。

### 5.2 MCP 检索工具接入（已完成）

为了让知识库能力真正进入溯源主流程，我们已经将各知识库的检索能力统一封装为 MCP Tool 形式。  
这意味着在多 Agent 协作过程中，任何需要查知识的步骤都可以通过标准化工具调用完成，而不需要在业务代码中重复写查询逻辑。

当前已完成 MCP 化的检索能力覆盖如下：

1. 资产库检索工具：用于按实体键快速确认“对象是什么资产、归属谁、重要程度如何”，支撑告警目标定性。  
2. 实时标注检索工具：用于识别短期运营语义（如临时白名单、演练标记、观察期进程），避免误判。  
3. 历史案例检索工具：用于在新告警出现时检索相似处置经验，辅助研判方向选择。  
4. 安全知识检索工具：用于补充术语、攻击技术和漏洞背景，增强解释能力并降低幻觉风险。  
5. 解释分析工具：用于承接非查询类分析任务。对于“如何理解这段输入”“这条告警应如何解读”这类问题，系统可通过通用分析工具直接调用 LLM 生成解释与研判建议。  

SOP 目前在运行时走 Captain 初始化路由链路（告警输入 + SOP 列表 -> LLM 选择参考流程）；  
其余知识库在运行时通过 MCP 工具直接按需检索。  
从整体能力上看，知识库已经实现“检索能力工具化”，并可在溯源全流程中复用。

新增的解释分析工具采用统一的抽象入参：`instruction` + `input`。  
其中，`instruction` 用于描述本次分析的目标（例如“请判断风险并给出处置建议”），`input` 用于承载待分析内容（例如告警 payload、执行结果文本或上下文片段）。  
这种设计避免工具被绑定到单一场景，使其既可用于告警 payload 分析，也可用于其他解释性任务。

### 5.3 MCP 化带来的价值

将知识库检索封装为 MCP Tool 后，系统获得了三个直接收益：

1. 接入统一：不同知识库的调用方式一致，降低 Agent 侧集成复杂度。  
2. 复用增强：同一检索工具或解释工具可被 Captain、Manager、Operator、Expert 在不同环节复用。  
3. 可演进性更好：后续新增知识类型时，只需新增对应 MCP server 与工具，不必改动主流程框架。

## 6. 初始化与数据组织

### 6.1 数据目录

当前种子数据按知识类型拆分在 `sql_data/knowledge` 下：

1. `sql_data/knowledge/sop/sop_seed.json`
2. `sql_data/knowledge/assets/assets_seed.json`
3. `sql_data/knowledge/runtime_annotations/runtime_annotations_seed.json`
4. `sql_data/knowledge/kb_cases/kb_cases_seed.json`
5. `sql_data/knowledge/kb_security_knowledge/kb_security_knowledge_seed.json`

### 6.2 统一初始化脚本

已合并为单入口脚本：`tools/init_knowledge_base.py`  
支持：

1. `--target sqlite`  
2. `--target qdrant`  
3. `--target all`  

并可分别控制 `recreate` 行为，便于本地重建与回归测试。

## 7. 当前状态总结

截至本次更新，知识库体系已具备以下能力：

1. SQLite 与 Qdrant 的职责边界清晰。  
2. SOP、资产、实时标注、案例、安全知识均已具备对应存储模型。  
3. Captain 初始化链路已接入新版 SOP 路由机制。  
4. 各知识库的检索能力已完成 MCP Tool 化接入（SOP 为初始化路由链路，其余库通过 MCP 运行时调用）。  
5. 已新增通用解释分析 MCP 工具，用于非查询类研判任务（如告警 payload 分析）。  
6. 初始化脚本已统一，支持一键导入全量知识。  

这为后续“基于知识的多 Agent 协同决策”打下了稳定的数据与调用基础。
