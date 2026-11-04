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
| `scripts/codex_bind_window.sh` | open the 30-minute `/codexbind` window |
| `scripts/receive_bot_token.py` | optional: receive a secret from a computer over `adb forward` |
| `scripts/tools_mcp.py` | stdio MCP server Codex starts to reach the gateway |
| `start.sh`, `stop.sh` | background start (PID file, wake lock) / graceful stop |

```bash
.venv/bin/python -m unittest discover -s tests     # offline, no keys
tail -n 20 logs/bot.log                            # bot_ready = connected; reply_sent = answer delivered
```

## 简体中文

Telegram 机器人本体。一套代码按实例 `.env` 里的 `BOT_ROLE` 运行三种角色：`deepseek`（`~/ai-council`）、`claude`（`~/ai-claude`）、`codex`（`~/ai-codex`）。安装见 [../docs/SETUP.zh-CN.md](../docs/SETUP.zh-CN.md)，使用见 [../docs/USAGE.zh-CN.md](../docs/USAGE.zh-CN.md)。上表列出了各文件的作用；测试离线运行、不需要密钥。
