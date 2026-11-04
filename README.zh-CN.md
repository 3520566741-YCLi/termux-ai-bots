# Termux AI 议事会

[English](README.md) | **简体中文**

在一部安卓手机的 Termux 里 24 小时运行三个 AI 助手，同在一个 Telegram 群。不需要服务器、不开公网端口、不需要 root。

| 机器人 | 后端 | 计费 | 能做什么 |
|---|---|---|---|
| **DeepSeek** | DeepSeek API（`deepseek-flash`） | 按 token（人民币） | 问答、看图、联网搜索（Tavily）、读你的 GitHub、读写 Notion、主持讨论 |
| **Claude** | Anthropic API（用 `httpx` 直接发 HTTPS） | 按 token（美元） | 问答、看图、官方网页搜索、读你的 GitHub |
| **Codex** | 用**你的 ChatGPT 订阅**登录的 `codex` 命令行 | 订阅额度 | 问答、看图、`--search`、读你的 GitHub、`/code`（改仓库并推 `codex/` 分支）、把文件存到 GitHub —— **只给主人（owner）用** |

Claude 用纯 HTTP 调用，是因为官方 Python SDK 依赖的 Rust 扩展（pydantic-core、jiter）在安卓上没有预编译包。

## 功能

**群里的分工**（机器人设为管理员后都能看到群里每条消息）：

- 群里普通消息 → 只由 **DeepSeek** 回答。
- `@example_claude_bot …`、`/ask@example_claude_bot …` 或回复 Claude 的消息 → 只由 **Claude** 回答。Codex 同理。
- 私聊任何一个机器人都可以直接用。

**讨论**（由已绑定的主人发起，DeepSeek 机器人主持）：

- `讨论 话题` —— DeepSeek → Claude → Codex 轮流发言，默认 5 轮。
- `讨论 3轮 话题` —— 自己设轮数（最多 10 轮）。
- `讨论 3轮 Claude DeepSeek：话题` —— 只让点名的参加（名字后面要有冒号）。
- `停止讨论` —— 当前发言结束后停止。
- 最后**一定由 Codex 把所有人的方案结合起来做综合总结**。
- Telegram 机器人看不到别的机器人的消息，所以由 DeepSeek 进程依次询问各个模型，再用各自机器人的 token 发到群里。

**工具**（密钥统一放在 `~/.config/ai-tools/.env`；没配置的就不提供）。**只有绑定的主人有工具和对话记忆**；允许的聊天里其他人只得到不带工具、不进记忆的普通回答，每小时最多 10 次：

- **联网搜索** —— DeepSeek 用 Tavily；Claude 用 Anthropic 官方网页搜索（每问最多 3 次）；Codex 用 `codex --search`。
- **GitHub（只读）** —— 列仓库、看目录、读文件、搜代码，包括私人仓库。Codex 拿不到令牌：它通过本机一次性通道 + MCP 查询。
- **Notion（只给 DeepSeek）** —— 搜索、读取、新建子页面、追加内容。不删除、不改写。只有主人这句话要求保存时才给写入工具，页面总是建在 `NOTION_PARENT_PAGE_ID` 下。说“把刚才的讨论整理一下保存到 Notion”即可。

**`/code` 等于在手机上执行任意代码**（仅主人）：每个任务都重新克隆、git 钩子不执行、用 proot 屏蔽密钥 —— 但 Codex 仍能联网、改没屏蔽的文件。见 [docs/SETUP.zh-CN.md](docs/SETUP.zh-CN.md) 第 14 步，并建议在 GitHub 上保护 `main`，让部署密钥只能推 `codex/*` 分支。

**ai-hub 控制台** —— 本机网页（只监听 127.0.0.1，需要 token；每次启动控制台都换新 token，打开浏览器前先核对控制台身份），可以启动/停止/重启每个机器人，看运行时长、今日提问数和估算费用、最近日志（已脱敏），还有守护进程把被安卓杀掉的机器人拉起来。

## 架构

```text
 Telegram 群 / 私聊
        │  长轮询（只有出站 HTTPS）
        ▼
┌───────────────────────────── 安卓手机 · Termux ──────────────────────────────┐
│                                                                              │
│  ~/ai-council  (BOT_ROLE=deepseek)  —— 真正的代码 + .venv                     │
│     ├─ 回答群里普通消息 ─────────────────────────► DeepSeek API               │
│     ├─ 主持“讨论”：依次问 DeepSeek、Claude、Codex，用各自 token 发言，          │
│     │  最后 Codex 总结                                                       │
│     └─ 工具：Tavily、GitHub（只读）、Notion                                   │
│                                                                              │
│  ~/ai-claude   (BOT_ROLE=claude)   —— 代码是指向 ~/ai-council 的符号链接       │
│     └─ 被点名时回答 ─────────────────────────────► Anthropic Messages API     │
│                                                                              │
│  ~/ai-codex    (BOT_ROLE=codex)    —— 代码是指向 ~/ai-council 的符号链接       │
│     └─ 仅主人：`proot codex exec` ───────────────► ChatGPT 订阅               │
│          （proot 把 .env、~/.ssh、ai-hub 与 ai-tools 配置挂成空）              │
│          GitHub 走本机通道 + MCP（令牌不交给 Codex）                           │
│                                                                              │
│  ~/.config/ai-tools/.env   GITHUB_TOKEN · TAVILY_API_KEY · NOTION_*          │
│  ai-hub (~/.local/bin/ai-hub)  启停/守护 · 网页控制台 :8765                   │
└──────────────────────────────────────────────────────────────────────────────┘
```

一套代码、三个实例：每个实例目录有自己的 `.env`、`logs/`、`memory_store/`；`~/ai-codex`、`~/ai-claude` 里的代码目录是指向 `~/ai-council` 的符号链接，更新一处即可。

## 费用（估算，请以官方价格页为准）

| 项目 | 控制台使用的价格 |
|---|---|
| DeepSeek `deepseek-flash` | 每百万 token：缓存命中 0.04 元、未命中 2 元、输出 8 元（工作日 9–12、14–18 点）；其他时间半价 |
| Claude Sonnet | 每百万 token：输入 $2、输出 $10、缓存读取 $0.2；网页搜索每次 $0.01。讨论里 Claude 每次发言约 1–2 美分 |
| Codex | 包含在 ChatGPT 订阅里（占用订阅的用量额度） |
| Tavily / GitHub / Notion / Telegram | 个人使用免费额度足够 |

价格写在 `~/.config/ai-hub/bots.json` 的 `pricing` 里，可以自己改。

## 需要什么

- 一部安卓手机（在华为 / HarmonyOS 手机上实测；arm64、Android 8 以上应该都行），一直充电、联网。
- 从 **F-Droid 或 GitHub** 安装 Termux 与 Termux:API（不要用 Google Play 版）。可选：Termux:Widget、Termux:X11。
- 手机能访问 Telegram、DeepSeek、Anthropic、OpenAI（有些地区需要在手机上开代理/VPN）。
- 账号：Telegram、DeepSeek API、Anthropic API、包含 Codex 的 ChatGPT 订阅。可选：GitHub、Tavily、Notion。

## 快速开始

按 **[docs/SETUP.zh-CN.md](docs/SETUP.zh-CN.md)** 一步步做（AI 助手也能照着做，见 [AGENTS.md](AGENTS.md)）。日常使用见 **[docs/USAGE.zh-CN.md](docs/USAGE.zh-CN.md)**。

密钥在手机上用 `scripts/set_secret.py` 输入（不显示，写进权限 600 的 `.env`）。不要把密钥粘贴到任何聊天里 —— Telegram 不行，你的 AI 助手对话框也不行。

## 目录结构

```text
ai-council/            机器人（一套代码，三种角色）
  agents/              deepseek/、claude/、codex/（问答 + /code）、tools.py、tool_gateway.py、files.py
  gateway/telegram/    bot.py：权限、分工、命令、讨论、日志
  orchestrator/        council.py（记忆）、discussion.py（多机器人讨论）
  scripts/             set_secret.py、find_chat_id.py、codex_bind_window.sh、receive_bot_token.py、tools_mcp.py
  tests/               离线测试（不需要密钥、不联网）
  start.sh、stop.sh、requirements.txt、.env.example
ai-hub/                控制台：ai_hub.py、web/、tests/、bots.example.json、AI-Bots 快捷方式、ensure.sh、ADDING_AI.md
extras/                可选：chinese-desktop/（XFCE 中文界面）、claude-desktop/（PRoot 里的 Claude 桌面版）
docs/                  SETUP、USAGE（English + 简体中文）
AGENTS.md              给 AI 编程助手的说明
```

## 许可证

License: to be chosen by the repo owner.（许可证由仓库所有者决定。）
