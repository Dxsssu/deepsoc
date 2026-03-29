# DeepSOC MCP 新增内容说明

本文档参考 `docs/MCP_Refactor_Guide.md`，重点不是重复说明整次改造过程，而是专门梳理“此次新增的主要内容”。

为避免歧义，下文沿用需求中的“`SORA` 剧本”说法；但从代码和历史文档来看，系统原本使用的实际术语是 `SOAR playbook`。两者在本文中指代的是同一类历史能力形态。

---

## 1. 修改前的实现方式：SORA 剧本

在这次新增 MCP 动态工具能力之前，系统的自动化执行主要建立在 `SORA/SOAR` 剧本之上。也就是说，大模型并不是直接面向某个本地函数或某个可注册工具进行调用，而是围绕“已有剧本”来组织命令和执行流程。

当 `_captain` 下发任务后，`_manager` 会把任务拆解成动作，随后 `_operator` 进一步把动作转成机器可执行的命令。旧实现里，这类命令通常是 `playbook` 类型，执行所依赖的是剧本的标识信息，例如 `playbook_id` 和 `playbook_name`。最终由执行层对接 SOAR 客户端，把参数传入具体剧本。

典型的历史命令结构大致如下：

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

这种方式有一个很明显的特点：系统真正依赖的是“外部剧本平台已经存在什么能力”。换句话说，DeepSOC 自己并不直接定义工具，而是把工具能力外包给剧本体系。这样做在初期接入 SOAR 平台时很直接，但也带来了几个问题：

- 工具能力受限于外部剧本平台的定义方式。
- 参数结构以剧本为中心，而不是以代码中的函数签名为中心。
- 新增或调整能力时，往往要同时考虑剧本、提示词、执行逻辑三部分。
- 提示词里描述的能力和运行时真实可用能力，容易逐渐不一致。

从当前代码也能看出，这条旧链路已经被明确弱化。`app/services/executor_service.py` 里仍然能识别 `playbook` 类型，但通道已经标记为停用；与此同时，`app/services/operator_service.py` 中还保留了把旧 `playbook` 命令自动归一化为 `mcp` 命令的兼容逻辑。这说明本次新增并不是简单“换个名字”，而是把工具执行模型从“剧本中心”正式迁移到了“本地 MCP 工具中心”。

---

## 2. 如何读取已有的 SORA：直接写到提示词

旧方案里，系统要想让大模型“知道现在有哪些剧本可用”，最直接的做法就是把已有的 `SORA/SOAR` 剧本清单直接写进提示词。

具体来说，提示词模板中会预留一个占位区域，例如：

```xml
<playbook_list>
{playbook_list}
</playbook_list>
```

在生成 prompt 时，系统再从数据库背景项中读取历史剧本列表，并把这段文本整体替换进去。当前 `app/prompts/generate_prompt.py` 中依然可以看到这一层兼容保留：

```python
playbooks = Prompt.query.filter_by(name=BACKGROUND_PLAYBOOKS).first()
prompt = prompt.replace('{playbook_list}', playbooks.content if playbooks else '')
```

这意味着，旧模式下模型看到的“可用能力”本质上是一段静态文本，而不是运行时实时发现出来的真实工具目录。只要数据库里的这段背景文字没有同步更新，提示词和系统现状就可能脱节。

例如，一份典型的历史剧本背景可能像这样：

```yaml
playbooks:
  - id: 12321435630187042
    name: query_asset_info_by_ip
  - id: 12316887511154270
    name: General_IP_Threat_Intelligence_Query
  - id: 12302548181076025
    name: Web_SQL_Injection
```

这种方式的好处是实现简单，大模型一进来就能看到完整的能力清单；但缺点也很明显：

- 剧本内容需要人工维护，容易过期。
- 新增一个能力时，通常要额外修改提示词背景或数据库配置。
- 文本里写了“有这个能力”，并不等于运行时就一定能调用成功。
- 参数名、参数类型、默认值等信息不够结构化，模型只能“读文字猜规则”。

因此，这次新增内容的核心之一，就是不再把“工具描述”完全寄托在手工维护的提示词文本上，而是让代码自己成为能力定义的唯一真实来源。

---

## 3. FastMCP 封装

为了解决旧方案里“工具能力依赖外部剧本定义”的问题，这次新增把本地可调用能力统一收敛到了 `FastMCP` 封装上。

现在的实现方式是：在 `app/utils/mcp_servers/` 目录下，以 `*_server.py` 为单位定义 MCP Server；在每个 Server 文件中创建一个 `FastMCP` 实例；再通过 `@mcp.tool()` 暴露具体工具。这样做之后，工具的名称、描述、参数、返回结果都由代码直接定义，结构会更清晰，也更容易测试。

当前真实代码中的示例：

```python
from fastmcp import FastMCP

mcp = FastMCP("threat_intel_mcp")

@mcp.tool(name="ip_reputation_lookup", description="查询IP信誉评分、风险等级和标签")
def ip_reputation_lookup(ip: str, time_window_minute: int = 60) -> dict:
    ...
```

再比如另一个已经存在的工具：

```python
from fastmcp import FastMCP

mcp = FastMCP("ip_info_mcp")

@mcp.tool(name="ip_info_lookup", description="查询IP基础信息（归属、网络类型、ASN等）")
def ip_info_lookup(ip: str) -> dict:
    ...
```

这种封装方式带来了几个直接收益：

- 工具定义从“平台剧本配置”变成“本地代码定义”。
- 参数类型可直接从函数签名中获取，天然结构化。
- 描述信息和执行逻辑写在一起，不容易出现“文档是一套、实现是另一套”。
- 更符合 MCP 生态的标准接口，后续不管是扩展 server、增加 tool，还是做测试和调试，都更统一。

从研发体验看，这一步非常关键。以前新增能力，思路更像“我要去接入一个剧本”；现在新增能力，思路则变成“我要定义一个 MCP Tool”。这意味着整个系统的工具层开始从外部平台依赖，逐步转向本地代码驱动。

---

## 4. 自动读取并解析

本次新增最有价值的地方，不只是把工具改成了 `FastMCP` 写法，而是把“读取工具、展示工具、执行工具”整个过程都改成了自动化。

### 4.1 自动发现 Server

系统会自动扫描 `app/utils/mcp_servers/` 目录，并加载所有符合 `*_server.py` 命名规则的模块。随后，注册中心会在模块里查找 `FastMCP` 实例，默认会识别变量名为 `mcp` 的对象，同时也支持发现模块中其他 `FastMCP` 实例。

核心逻辑可以概括为：

```python
for module in pkgutil.iter_modules([str(current_dir)]):
    if module.name.startswith("_") or module.name in {"registry"}:
        continue
    if not module.name.endswith("_server"):
        continue

    imported_module = importlib.import_module(f"{package_name}.{module.name}")
    for mcp in _discover_fastmcp_instances(imported_module):
        _SERVER_REGISTRY[server_name] = mcp
```

这一步的意义在于，系统不需要手工注册每一个 server。只要文件放对位置、命名符合规则、内部确实定义了 `FastMCP` 实例，它就能被自动发现。

### 4.2 自动解析 Tool 元数据

在发现 server 之后，系统会通过 `list_tools()` 读取每个工具的名称、描述和参数 schema，再把 schema 转成更适合 prompt 使用的结构化目录。对应逻辑在 `app/utils/mcp_servers/registry.py` 中已经实现：

```python
tools = _run_async(server.list_tools())
schema = getattr(tool, "parameters", {}) or {}
```

随后系统会把参数信息整理成：

- 参数名 `name`
- 参数描述 `desc`
- 是否必填 `required`
- 参数类型 `type`
- 默认值 `default`

这样一来，大模型在提示词里看到的就不再只是“某某工具可用”，而是能看到这类工具究竟接收什么参数、哪些参数是必填、默认值是什么，理解成本会明显降低。

### 4.3 自动注入提示词

读取和解析完成后，系统会把当前实时工具目录渲染成 YAML，并注入到 `{mcp_tools}` 占位符中：

```python
dynamic_mcp_tools = render_tool_catalog_yaml()
prompt = prompt.replace('{mcp_tools}', dynamic_mcp_tools)
```

这一步很重要，因为它把“提示词里的工具清单”从手工文本，变成了运行时真实状态的直接投影。也就是说：

- 提示词看到什么工具，
- 基本就代表系统当前真的加载了什么工具。

相比旧的 `playbook_list` 文本注入，这种方式的一致性更高，也更不容易出现“模型以为能调，结果系统里没有”的问题。

### 4.4 自动执行 Tool

除了展示目录，本次新增还把执行路径打通了。当前命令执行链路已经变成：

`executor_service -> MCPToolService -> registry.execute_tool -> FastMCP.call_tool`

核心调用方式如下：

```python
invoke_result = mcp_service.execute_tool(
    server_name=str(server),
    tool_name=str(tool),
    params=command.command_params if isinstance(command.command_params, dict) else {}
)
```

最终由注册中心调用：

```python
tool_result = _run_async(server.call_tool(str(tool_name), safe_params))
```

这意味着，工具的“发现、描述、执行”现在都围绕同一套注册中心完成，链路更短，也更容易定位问题。

### 4.5 当前自动发现结果

按照当前代码和实际冒烟测试结果，系统已经能自动读到如下 MCP 能力：

```json
{
  "mcp_servers": [
    {
      "name": "ip_info_mcp",
      "tools": [
        { "name": "ip_info_lookup" }
      ]
    },
    {
      "name": "threat_intel_mcp",
      "tools": [
        { "name": "ip_reputation_lookup" }
      ]
    }
  ]
}
```

对应的测试脚本是：

```bash
.venv/bin/python tools/test_mcp_catalog.py --json
```

当前实测结果为：已发现 `2` 个 MCP Server、`2` 个 MCP Tool。

---

## 5. 方便扩展

如果说旧方案的扩展方式是“新增一个剧本，再同步提示词和执行链路”，那么这次新增后的扩展方式就简单得多了。

现在新增能力的一般步骤是：

1. 在 `app/utils/mcp_servers/` 下新增一个 `*_server.py` 文件。
2. 在文件中创建一个 `FastMCP("server_name")`。
3. 使用 `@mcp.tool()` 定义一个或多个工具。
4. 重新运行系统或执行测试脚本验证是否被自动发现。

一个最小示例如下：

```python
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

这个文件一旦放进目录，系统理论上就能在下次加载时自动识别它；不需要再去手工补一份长篇提示词，也不需要额外维护一张静态工具清单。

这对后续扩展非常有帮助，主要体现在以下几个方面：

- 扩展入口统一，研发只需要关注 `mcp_servers` 目录。
- 工具定义、参数说明、执行逻辑都在代码里，维护成本更低。
- prompt 展示内容自动跟随运行时结果变化，不容易遗漏。
- 后续增加更多业务域时，可以按 server 维度拆文件，边界更清晰。
- 工具可以先做本地冒烟测试，再接入完整多智能体流程，验证更轻量。

如果后续团队需要持续扩展，比较推荐遵循下面这套约定：

- 一个业务域对应一个 server 文件，例如 `threat_intel_server.py`、`asset_server.py`。
- tool 名称尽量稳定，避免频繁改名影响提示词理解和执行链路。
- 参数类型尽量明确，让 FastMCP 自动生成更完整的 schema。
- 每新增一个 server 或 tool，都执行一次：

```bash
.venv/bin/python tools/test_mcp_catalog.py
```

总体来看，这次新增的价值并不只是“多了几个 MCP 工具”，而是把系统的工具层从“依赖静态剧本文本”升级成了“代码定义、自动发现、自动解析、自动执行、易于扩展”的一整套能力。这为后续继续补充更多安全查询、研判、处置类工具打下了一个更稳的基础。
