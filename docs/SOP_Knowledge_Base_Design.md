# SOP Knowledge Base Design (Qdrant)

## 1. 目标
- 支持多类型溯源知识统一存储（SOP/案例/IOC等）。
- 先落地 `SOP` 类型：针对不同告警类型给出总体溯源思路。
- 保证后续扩展时尽量少改代码。

## 2. Qdrant 设计原则
- 使用向量语义检索定位“最相近”的处置思路。
- 使用 `sop_index` 作为SOP检索索引字段，与event文本向量化后做相似度比对。
- 使用确定性 point_id（基于 type + sop_name + version）实现幂等 upsert。

## 3. Collection 规划
- 默认通用 collection：`traceback_knowledge`（用于未来统一检索）。
- 当前SOP专用 collection：`sop_knowledge_base`（便于快速起步和隔离演进）。
- 向量参数：
  - `distance = cosine`
  - `vector_size = KB_VECTOR_SIZE`（默认 384）

## 4. SOP 文档结构（payload）
```json
{
  "knowledge_type": "sop",
  "sop_name": "异常登录尝试（服务器/网关）溯源与响应 SOP",
  "sop_index": "异常登录 登录失败 认证失败 SSH RDP VPN 网关 服务器 暴力破解 爆破",
  "workflow_steps": [
    {
      "step_name": "阶段一：查询威胁情报（定性攻击源）",
      "step_content": "查询源IP威胁情报与内部历史行为..."
    }
  ],
  "version": "1.0.0",
  "source": "deepsoc_sop_seed",
  "updated_at": "2026-04-07T00:00:00Z"
}
```

## 5. 检索策略
- Query 向量检索：使用事件文本（`event_name + event_message`）作为查询向量。
- 过滤条件：
  - 必选：`knowledge_type=sop`
- 返回 Top-K：默认 `KB_DEFAULT_TOP_K=3`。

## 6. Embedding 策略
- 默认 `KB_EMBEDDING_PROVIDER=hash`（离线可用，便于快速演示）。
- 可切换 `openai` provider（通过 `EMBEDDING_MODEL / EMBEDDING_API_KEY`）。
- 统一由 `KnowledgeBaseService` 封装，业务层不关心 embedding 实现。
