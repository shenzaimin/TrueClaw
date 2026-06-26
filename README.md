# TrueClaw

Python 实现的自主智能体运行时 MVP：网关、Webhook/Telegram 通道、Mock/OpenAI 兼容 LLM、工具调用、插件通道、调度器与 CLI 运维面。

## 仓库内容

| 路径 | 说明 |
| --- | --- |
| `src/` | TrueClaw 核心与插件包 |
| `scripts/` | `run.sh`、`ci-verify.sh` |
| `tests/` | 单元测试 |
| `examples/` | MCP 与独立插件示例 |
| `workspace/` | `read_file` 验收 fixture |

本地配置文件（如 `trueclaw.local.json`）默认不提交，请用 `init` 生成或自行维护。

## 环境要求

- Python **3.11+**
- 零第三方运行时依赖（标准库实现）
- 默认使用 **mock** 提供商，无需 API Key 即可离线验收

## 快速开始（5 分钟）

```bash
# 克隆后进入仓库根目录
cd python-openclaw

# 推荐：使用仓库脚本（自动 PYTHONPATH + Python 版本检查）
./scripts/run.sh --config ./trueclaw.local.json doctor

# 方式 A：PYTHONPATH 开发运行
export PYTHONPATH=src
python3 -m trueclaw --config ./trueclaw.local.json doctor

# 方式 B：可编辑安装（entry points 元数据完整，需 Python 3.11+）
python3 -m pip install -e .
python3 -m trueclaw --config ./trueclaw.local.json doctor
```

> `--config` 可写在子命令前或后，例如 `trueclaw doctor --config ./trueclaw.local.json` 与 `trueclaw --config ./trueclaw.local.json doctor` 均可。

生成本地配置（可选）：

```bash
./scripts/run.sh --config ./trueclaw.local.json init --force
./scripts/run.sh --config ./trueclaw.local.json config validate
```

或：

```bash
PYTHONPATH=src python3 -m trueclaw init --force --config ./trueclaw.local.json
PYTHONPATH=src python3 -m trueclaw --config ./trueclaw.local.json config validate
```

启动网关（前台）：

```bash
PYTHONPATH=src python3 -m trueclaw --config ./trueclaw.local.json gateway run
```

另开终端，Webhook 入站示例：

```bash
curl -s -X POST http://127.0.0.1:18890/webhook \
  -H 'Content-Type: application/json' \
  -d '{"sender_id":"u1","chat_id":"c1","content":"hello"}'
```

> 注意：请使用 `python3`（3.11+）。系统默认 `pip` 若指向 3.9 等旧版本，请改用 `python3 -m pip install -e .`。

## 集成验收

一条命令跑完全部离线验收（静态检查 + Webhook 端到端 + 工具链路）：

```bash
PYTHONPATH=src python3 -m trueclaw --config ./trueclaw.local.json verify
```

分套件运行：

```bash
# L1 静态层：配置、通道发现、插件、工具注册表
PYTHONPATH=src python3 -m trueclaw --config ./trueclaw.local.json verify --suite static

# L2 Webhook 对话链路（mock LLM）
PYTHONPATH=src python3 -m trueclaw --config ./trueclaw.local.json verify --suite webhook

# L2 工具链路（read_file + mock 回灌）
PYTHONPATH=src python3 -m trueclaw --config ./trueclaw.local.json verify --suite tools
```

CI 脚本等价命令：

```bash
./scripts/ci-verify.sh
```

### 验收矩阵

| 层级 | 命令 | 覆盖能力 |
| --- | --- | --- |
| L1 静态 | `verify --suite static` | 配置校验、通道/插件发现、`read_file` 注册、fixture |
| L2 对话 | `verify --suite webhook` 或 `gateway smoke` | WS 控制面、Webhook 入站/出站、会话、metrics |
| L2 工具 | `verify --suite tools` | Mock 触发 `read_file`，工具结果回灌到出站 |
| L2 Slack | `verify --suite slack` | url_verification、事件入站、会话落盘 |
| L2 调度 | `verify --suite scheduler` | 手动 `wake` → Agent 处理 |
| L2 MCP stdio | `verify --suite mcp_stdio` | 子进程 MCP echo server 工具调用 |
| L2 MCP HTTP | `verify --suite mcp_http` | HTTP JSON-RPC MCP echo 工具调用 |
| L2 WS 订阅 | `verify --suite ws` | `gateway.subscribe` 模式过滤 |
| L2 WS 事件 | `gateway smoke` 含 `gateway.ws.outbound_event` | 出站 `channel.outbound` 推送到 WebSocket |
| 运维 | `doctor` | 网关/通道/LLM/会话/插件/单实例锁 |
| 插件 | `plugins list` / `plugins doctor` | `trueclaw.plugins.channel` entry points |

### 三条验收旅程

1. **纯对话**：`gateway run` → Webhook/curl 发两轮消息
2. **工具**：`verify --suite tools` 或消息含 `read_file:hello.txt`
3. **异常**：双开 `gateway run`，应看到单实例锁退出码 `2`

## 常用命令

```bash
PYTHONPATH=src python3 -m trueclaw --config ./trueclaw.local.json plugins list
PYTHONPATH=src python3 -m trueclaw --config ./trueclaw.local.json tools list
PYTHONPATH=src python3 -m trueclaw --config ./trueclaw.local.json tools mcp list
PYTHONPATH=src python3 -m trueclaw --config ./trueclaw.local.json agent chat -m "hello"
PYTHONPATH=src python3 -m trueclaw --config ./trueclaw.local.json gateway ctl --action gateway.ping
PYTHONPATH=src python3 -m trueclaw --config ./trueclaw.local.json gateway ctl --action session.list --limit 5
PYTHONPATH=src python3 -m trueclaw --config ./trueclaw.local.json wake list
```

## 配置要点

| 段 | 说明 |
| --- | --- |
| `gateway` | WS 端口 `port`，控制面 `port+1`，`instanceLock` 防多实例 |
| `providers.mock` | 离线 mock；`openai` 等需 `apiKey` |
| `channels.webhook` | HTTP 入站/出站；smoke 使用 28890 |
| `channels.echo` | 示例插件通道（默认关闭） |
| `channels.slack` | Slack Events API 骨架（默认关闭） |
| `gateway.pushOutboundEvents` | 向 WS 客户端推送 `channel.outbound` 事件 |
| `tools.workspaceDir` | `read_file` 工作区根目录 |
| `mcp.servers.*` | MCP 桥接（`mock` / `stdio`） |
| `scheduler.mode` | 集成阶段建议 `off`，稳定后再开 |

示例配置：运行 `init` 生成，或使用本地 `trueclaw.local.json`（已 gitignore）。

## 项目结构

```
src/trueclaw/          # 核心包
src/trueclaw_plugins/  # 内置插件（echo、slack）
scripts/               # run.sh、ci-verify.sh
examples/              # MCP / 插件示例
workspace/             # 工具测试 fixture
tests/                 # 单元测试
```

## 安全提示

- 勿将真实 `apiKey` / `botToken` 提交到仓库
- Webhook 生产环境请配置 `verifyToken` 与 `signingSecret`
- `read_file` 受 `workspaceDir` 限制，勿指向系统敏感目录

## 故障排查

克隆后最快验证：

```bash
./scripts/run.sh --config ./trueclaw.local.json doctor
bash scripts/ci-verify.sh
```

| 现象 | 处理 |
| --- | --- |
| 配置不合法 | `trueclaw config validate` |
| 网关起不来 / 端口占用 | `trueclaw doctor`（gateway、instance_lock） |
| `gateway already running` | 删除 `gateway-{port}.lock` 或结束占用进程 |
| Webhook 无响应 | `doctor` 看 webhook / webhook_health |
| Slack challenge 失败 | `verify --suite slack` |
| 插件未加载 | `plugins doctor`；确认 `pip install -e .` 或 `PYTHONPATH=src` |
| MCP 异常 | `tools mcp doctor` |
| 定时任务未触发 | `wake list`；`scheduler.mode=inprocess` |
| smoke 端口冲突 | smoke 用 28789/28890，勿与 `gateway.port` 冲突 |

## 能力边界

| 领域 | 说明 | 状态 |
| --- | --- | --- |
| 多通道抽象 | 统一适配器 + 能力矩阵 | ✅ Webhook/Telegram/Slack/Echo 插件 |
| Agent 主循环 | 工具回灌 + 流式 | ✅ Mock/OpenAI 兼容 + 流式 tool_calls |
| 插件系统 | entry points + 降级 | ✅ `trueclaw.plugins.channel` |
| CLI 运维 | init/validate/run/doctor/verify | ✅ 模块化 CLI（config/gateway/doctor/plugins/tools/wake） |
| MCP | stdio + HTTP | ✅ mock/stdio/http；SSE 长连接未实现 |
| WS 控制面 | ping/stats/subscribe/session.list + RBAC | ✅ 心跳/空闲断开/maxMessageBytes |
| 调度唤醒 | WakeContext + leader + wake_id 幂等 | ✅ 文件/Redis 跨进程 wake_id 门闩 |
| 会话存储 | 内存 MVP，可迁移 SQLite/Redis | ⚠️ 仅 `memory_store` |
| 网关心跳/空闲断开 | heartbeatIntervalSec + idleTimeoutSec | ✅ 已实现 |
| 权限 RBAC | viewer/operator/admin | ✅ action 级 RBAC + `gateway.auth` |
| Telegram 生产细节 | offset 持久化、附件占位 | ✅ offset 刷盘 + allowlist.reload |

**进阶未实现**：MCP SSE、SQLite/Redis 会话存储、邮箱唤醒。

## 许可证

见仓库许可证文件（若未单独声明，以项目维护者约定为准）。
