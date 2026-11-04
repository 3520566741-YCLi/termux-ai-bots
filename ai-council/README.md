# ai-council

**English** | [简体中文](#简体中文)

The Telegram bot. One codebase, three roles selected by `BOT_ROLE` in the instance's `.env`: `deepseek` (`~/ai-council`), `claude` (`~/ai-claude`), `codex` (`~/ai-codex`). Setup: [../docs/SETUP.md](../docs/SETUP.md). Usage: [../docs/USAGE.md](../docs/USAGE.md).

| Path | Purpose |
|---|---|
| `gateway/telegram/bot.py` | permissions, group routing, commands, discussions, event-only logging, polling |
| `agents/deepseek/client.py` | DeepSeek (OpenAI-compatible) with function calling |
| `agents/claude/client.py` | Anthropic Messages API over `httpx` (no SDK), web search, GitHub client tools |
| `agents/codex/client.py`, `coder.py` | `codex exec` in proot (Q&A, `/code`) |
| `agents/tools.py`, `tool_gateway.py` | shared GitHub / Tavily / Notion tools; local one-time gateway for Codex |
| `agents/files.py` | owner's files → GitHub `files` branch |
| `orchestrator/council.py`, `discussion.py` | per-chat memory; multi-bot discussions |
| `scripts/set_secret.py` | enter a secret on the phone (hidden input, `.env` mode 600) |
| `scripts/find_chat_id.py` | list chats that messaged the bot |
| `scripts/codex_bind_window.sh` | open the 30-minute `/codexbind` window and print the one-time 6-digit code (`/codexbind <code>`) |
| `scripts/receive_bot_token.py` | optional: receive a secret from a computer over `adb forward`; needs `--nonce <hex>`, the sender sends `<nonce> <secret>` |
| `scripts/tools_mcp.py` | stdio MCP server Codex starts to reach the gateway |
| `start.sh`, `stop.sh` | background start (PID file, wake lock) / graceful stop |

Security behaviour (details in [../docs/SETUP.md](../docs/SETUP.md) step 14 and "Security notes"):

- Only the bound owner gets tools (GitHub, search, Notion) and memory; others get plain answers, 10 per hour per chat.
- Notion writes only when the owner's message has a save intent, always under `NOTION_PARENT_PAGE_ID`.
- `/code` is full code execution on the phone: fresh clone per task, commit/push from a separate clean clone, git hooks and fsmonitor disabled, secrets hidden via proot (refuses to run without proot). Codex can still use the network and change unhidden files.

```bash
.venv/bin/python -m unittest discover -s tests     # offline, no keys
tail -n 20 logs/bot.log                            # bot_ready = connected; reply_sent = answer delivered
```

## 简体中文

Telegram 机器人本体。一套代码按实例 `.env` 里的 `BOT_ROLE` 运行三种角色：`deepseek`（`~/ai-council`）、`claude`（`~/ai-claude`）、`codex`（`~/ai-codex`）。安装见 [../docs/SETUP.zh-CN.md](../docs/SETUP.zh-CN.md)，使用见 [../docs/USAGE.zh-CN.md](../docs/USAGE.zh-CN.md)。上表列出了各文件的作用；测试离线运行、不需要密钥。

安全相关行为（详见 [../docs/SETUP.zh-CN.md](../docs/SETUP.zh-CN.md) 第 14 步和“安全说明”）：

- 只有绑定的主人有工具（GitHub、搜索、Notion）和对话记忆；其他人只得到普通回答，每人每个聊天每小时最多 10 次。
- `/codexbind` 需要 `scripts/codex_bind_window.sh` 显示的 6 位绑定码（`/codexbind 绑定码`）。
- `scripts/receive_bot_token.py` 需要 `--nonce <十六进制>`，发送方发 `<nonce> <密钥>`；`scripts/set_secret.py` 不需要 nonce。
- 只有主人这句话有保存意图时才写 Notion，而且总写在 `NOTION_PARENT_PAGE_ID` 下。
- `/code` 等于在手机上执行任意代码：每次重新克隆、在另一个干净克隆里提交推送、git 钩子和 fsmonitor 不执行、用 proot 屏蔽密钥（没有 proot 就拒绝）。Codex 仍能联网、改没屏蔽的文件。
