# DeepSOC MCP 改造说明（从 SOAR 剧本到 FastMCP 动态工具）

本文档详细说明本次多智能体系统在“工具执行层”上的改造过程，重点覆盖：

1. 改造前代码的实现方式（SOAR 剧本、剧本信息写入提示词）
2. 改造后代码的实现方式（FastMCP 封装、自动发现、易扩展）
3. 对比总结与后续扩展建议

---

## 第一部分：改造前代码是怎么写的

> 关键词：`SOAR 剧本驱动`、`提示词内嵌剧本清单`、`执行层依赖 playbook_id`

### 1. SOAR 剧本执行链路（改造前）

改造前的核心思路是：

- `_operator` 输出 `command_type: playbook`
- `_executor` 根据 `playbook` 分支调用 SOAR 客户端
- 最终把 SOAR 返回结果写入 `Execution`

典型链路如下：

1. `_captain` 下发 `TASK`
2. `_manager` 拆成 `ACTION`
3. `_operator` 生成 `COMMAND`（其中 `command_type=playbook`）
4. `_executor` 调用 `playbook_service -> SOARClient`

#### 示例 1：历史命令结构（playbook）

```yaml
commands:
  - command_type: playbook
    command_name: 操作系统登录日志查询
    command_assignee: _executor
    command_entity:
      playbook_id: 12302548181076029
      playbook_name: os_login_log_query
    command_params:
      ip: 211.14.168.179
      time_window_minute: 10
```

这个结构的特点是：执行依赖 `playbook_id/playbook_name`，工具调用能力和参数语义由 SOAR 剧本定义。

#### 示例 2：执行器分支（改造前语义）

```python
if command.command_type == 'playbook':
    result = execute_playbook_command(command)
elif command.command_type == 'manual':
    result = handle_manual_command(command)
```

这一写法直接把“自动化执行能力”绑定到了 SOAR 的剧本体系。

---

### 2. 剧本信息直接写到提示词里（改造前）

改造前另一个关键点是：

- 提示词里有大段 `SOAR 安全剧本能力清单`
- 由 `{playbook_list}` 注入到 `_manager/_operator` 提示词中
- LLM 的工具选择强依赖这段静态文本

#### 示例 1：提示词占位符（历史）

```xml
<playbook_list>
{playbook_list}
</playbook_list>
```

这意味着模型可见的“可调用能力”来源于提示词文本，而不是运行时真实工具注册状态。

#### 示例 2：背景清单（历史）

```yaml
### SOAR 安全剧本能力清单
playbooks:
  - id: 12321435630187042
    name: query_asset_info_by_ip
  - id: 12316887511154270
    name: General_IP_Threat_Intelligence_Query
  - id: 12302548181076025
    name: Web_SQL_Injection
```

这种方式的问题是：

- 剧本列表和真实可用能力可能不一致（易过时）
- 每次新增能力都要改提示词内容
- 配置和代码耦合在文本层，验证成本较高

---

## 第二部分：修改之后是怎么写的

> 关键词：`FastMCP @mcp.tool()`、`运行时自动发现`、`新增模块即扩展`

### 1. 使用 FastMCP 封装 MCP 工具

改造后，不再手写自定义装饰器协议，而是统一使用 FastMCP 官方封装方式：

- 每个 MCP Server 模块定义一个 `FastMCP` 实例
- 每个 Tool 通过 `@mcp.tool()` 暴露

当前示例实现文件：

- `app/utils/mcp_servers/threat_intel_server.py`

#### 示例：IP 信誉查询工具（当前实现）

```python
from fastmcp import FastMCP

mcp = FastMCP("threat_intel_mcp")

@mcp.tool(name="ip_reputation_lookup", description="查询IP信誉评分、风险等级和标签")
def ip_reputation_lookup(ip: str, time_window_minute: int = 60) -> dict:
    ...
```

这个实现的好处是：

- 工具签名和参数天然结构化
- 与 MCP 生态接口一致
- 工具行为由代码定义，便于测试与复用

---

### 2. 自动读取已封装的 MCP Server 和 Tools

改造后不再从提示词数据库里读取工具定义，而是运行时自动发现：

- 自动扫描目录：`app/utils/mcp_servers/`
- 自动加载模块规则：`*_server.py`
- 自动识别模块中的 `FastMCP` 实例（通常变量名为 `mcp`）
- 使用 `list_tools()` 获取工具元数据
- 使用 `call_tool()` 执行工具

核心实现文件：

- `app/utils/mcp_servers/registry.py`
- `app/services/mcp_tool_service.py`
- `app/prompts/generate_prompt.py`

#### 示例 1：自动生成提示词中的工具清单

`generate_prompt` 现在会把动态目录注入 `{mcp_tools}`：

```python
from app.utils.mcp_servers.registry import render_tool_catalog_yaml

dynamic_mcp_tools = render_tool_catalog_yaml()
prompt = prompt.replace('{mcp_tools}', dynamic_mcp_tools)
```

这表示：提示词看到的工具列表 = 当前真实加载到的 FastMCP 工具，而不是手工写死文本。

#### 示例 2：执行时按 server/tool 动态调用

```python
tool_result = _run_async(server.call_tool(str(tool_name), safe_params))
```

执行器通过 `MCPToolService -> registry.execute_tool` 间接调用该逻辑，返回结构化结果给后续链路。

#### 示例 3：当前读取结果（实测）

运行：

```bash
.venv/bin/python tools/test_mcp_catalog.py --json
```

得到（节选）：

```json
{
  "mcp_servers": [
    {
      "name": "threat_intel_mcp",
      "tools": [
        {
          "name": "ip_reputation_lookup"
        }
      ]
    }
  ]
}
```

---

### 3. 方便扩展：新增模块即可完成注册

改造后的扩展方式非常直接：

1. 在 `app/utils/mcp_servers/` 下新增一个 `*_server.py`
2. 在该文件里创建 `FastMCP("你的server名")`
3. 用 `@mcp.tool()` 定义工具
4. 完成（无需再改提示词大文本、无需手动维护工具清单）

#### 示例：新增一个资产查询 MCP Server（示例写法）

```python
# app/utils/mcp_servers/asset_server.py
from fastmcp import FastMCP

mcp = FastMCP("asset_mcp")

@mcp.tool(name="asset_lookup_by_ip", description="根据IP查询资产信息")
def asset_lookup_by_ip(ip: str) -> dict:
    return {
        "ip": ip,
        "asset_name": "mail-gateway-01",
        "owner": "SOC Team"
    }
```

放入目录后，系统会在下次加载时自动发现；
`tools/test_mcp_catalog.py` 也能直接读到新 server/tool。

---

## 第三部分：对比总结与落地建议

### A. 改造前后对照

| 维度 | 改造前 | 改造后 |
|---|---|---|
| 工具定义来源 | SOAR 剧本（外部体系） | FastMCP `@mcp.tool()`（本地代码） |
| 工具清单来源 | 提示词静态文本 | 运行时动态发现 |
| 扩展成本 | 改剧本 + 改提示词 + 对齐参数 | 新增 `*_server.py` + `@mcp.tool()` |
| 一致性 | 可能“提示词有、系统无” | 目录中有代码即可发现 |
| 可测试性 | 偏流程联调 | 可脚本化单测/冒烟测试 |

### B. 当前已完成的关键落地点

- 已下线 `default_prompts.py` 中 SOAR 剧本清单文本
- 已将 MCP 注册方式切换为 FastMCP 官方 `@mcp.tool()`
- 已实现自动扫描 `app/utils/mcp_servers/*_server.py`
- 已提供测试脚本：`tools/test_mcp_catalog.py`

### C. 推荐团队开发规范（建议）

1. 每个业务域一个 server 文件（如 `threat_intel_server.py`、`asset_server.py`）
2. tool 名称稳定、可读、可追踪（避免频繁改名）
3. 参数尽量显式类型化（便于 FastMCP 生成 schema）
4. 每新增工具后必须跑一次：

```bash
.venv/bin/python tools/test_mcp_catalog.py
```

5. 对高风险工具（封禁、隔离、删除）建议单独加人工确认链路（`manual` fallback）

---

## 附：当前相关代码位置

- 动态工具注册与发现：`app/utils/mcp_servers/registry.py`
- 威胁情报 MCP Server 示例：`app/utils/mcp_servers/threat_intel_server.py`
- MCP 调用入口：`app/services/mcp_tool_service.py`
- Prompt 动态注入 MCP 工具：`app/prompts/generate_prompt.py`
- MCP 目录读取测试脚本：`tools/test_mcp_catalog.py`

