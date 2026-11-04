# Termux AI Council

**English** | [简体中文](README.zh-CN.md)

Three AI assistants in one Telegram group, running 24/7 on an Android phone in Termux. No server, no open ports, no root.

| Bot | Backend | Billing | Can do |
|---|---|---|---|
| **DeepSeek** | DeepSeek API (`deepseek-flash`) | pay per token (CNY) | chat, images, web search (Tavily), read your GitHub, read/write Notion, hosts discussions |
| **Claude** | Anthropic API (raw HTTPS via `httpx`) | pay per token (USD) | chat, images, official web search, read your GitHub |
| **Codex** | the `codex` CLI logged in with **your ChatGPT subscription** | subscription quota | chat, images, `--search`, read your GitHub, `/code` (edit a repo and push a `codex/` branch), save files to GitHub — **owner only** |

Claude uses plain HTTP because the official Python SDK pulls in Rust extensions (pydantic-core, jiter) that have no Android wheels.

## What it does

**Group routing** (every bot may see every group message once they are admins):

- A plain group message → answered by **DeepSeek** only.
- `@example_claude_bot …`, `/ask@example_claude_bot …`, or a reply to a Claude message → answered by **Claude** only. Same for Codex.
- Private chats with any bot always work.

**Discussions** (started by the bound owner, hosted by the DeepSeek bot):

- `讨论 话题` — DeepSeek → Claude → Codex take turns, default 5 rounds.
- `讨论 3轮 话题` — set the number of rounds (max 10).
- `讨论 3轮 Claude DeepSeek：话题` — only the named bots speak (note the colon after the names).
- `停止讨论` — stop after the current speaker.
- At the end **Codex always writes a combined summary** of everyone's proposals.
- Telegram bots cannot see each other's messages, so the DeepSeek process asks each model in turn and posts with each bot's own token.

**Tools** (keys live in one shared file, `~/.config/ai-tools/.env`; anything not configured is simply not offered). **Only the bound owner gets tools and memory**; everyone else in an allowed chat gets plain answers without tools or memory, limited to 10 per hour:

- **Web search** — DeepSeek via Tavily; Claude via Anthropic's server-side web search (≤ 3 per question); Codex via `codex --search`.
- **GitHub (read-only)** — list repos, browse directories, read files, search code, including private repos. Codex never sees the token: it talks to a local one-time gateway over MCP.
- **Notion (DeepSeek only)** — search, read, create sub-pages, append. Never deletes or rewrites. Write tools are offered only when the owner's message asks to save, and pages always go under `NOTION_PARENT_PAGE_ID`. Say "save this discussion to Notion".

**`/code` is full code execution on the phone** (owner only): every task uses a fresh clone, git hooks are disabled, secrets are hidden via proot — but Codex can still reach the network and change unhidden files. See [docs/SETUP.md](docs/SETUP.md) step 14, and protect `main` on GitHub so the deploy key can only push `codex/*` branches.

**ai-hub console** — a local web page (127.0.0.1 only, protected by a token that rotates each time the console starts; the console's identity is verified before the browser opens) to start/stop/restart each bot, see uptime, today's requests and estimated cost, recent (redacted) logs, and a watchdog that restarts bots killed by Android.

## Architecture

```text
 Telegram group / private chats
        │  long polling (outbound HTTPS only)
        ▼
┌─────────────────────────── Android phone · Termux ───────────────────────────┐
│                                                                              │
│  ~/ai-council  (BOT_ROLE=deepseek)  ── real code + .venv                     │
│     ├─ answers plain group messages ─────────────► DeepSeek API              │
│     ├─ hosts 讨论: asks DeepSeek, Claude, Codex in turn, posts with each      │
│     │  bot's token, Codex writes the summary                                 │
│     └─ tools: Tavily, GitHub (read-only), Notion                             │
│                                                                              │
│  ~/ai-claude   (BOT_ROLE=claude)   ── symlinks to ~/ai-council code          │
│     └─ answers when addressed ───────────────────► Anthropic Messages API    │
│                                                                              │
│  ~/ai-codex    (BOT_ROLE=codex)    ── symlinks to ~/ai-council code          │
│     └─ owner only: `proot codex exec` ───────────► ChatGPT subscription      │
│          (proot hides .env, ~/.ssh, ai-hub and ai-tools config)              │
│          GitHub via local gateway + MCP (token never handed to Codex)        │
│                                                                              │
│  ~/.config/ai-tools/.env   GITHUB_TOKEN · TAVILY_API_KEY · NOTION_*          │
│  ai-hub (~/.local/bin/ai-hub)  start/stop/watchdog · web console :8765       │
└──────────────────────────────────────────────────────────────────────────────┘
```

One codebase, three instances: each instance directory has its own `.env`, `logs/` and `memory_store/`; the code directories in `~/ai-codex` and `~/ai-claude` are symlinks to `~/ai-council`, so you update one place.

## Costs (estimates — check the providers' current price pages)

| Item | Price used by the console |
|---|---|
| DeepSeek `deepseek-flash` | per 1M tokens: cache hit ¥0.04, miss ¥2, output ¥8 (weekday 9–12/14–18); half off otherwise |
| Claude Sonnet | per 1M tokens: input $2, output $10, cache read $0.2; web search $0.01 each. A discussion turn is roughly 1–2 US cents |
| Codex | included in your ChatGPT plan (counts against its usage limits) |
| Tavily / GitHub / Notion / Telegram | free tiers are enough for personal use |

Prices live in `~/.config/ai-hub/bots.json` (`pricing`), so you can correct them yourself.

## Requirements

- An Android phone (tested on a Huawei / HarmonyOS phone; any arm64 Android 8+ should work), always charging and online.
- Termux and Termux:API from **F-Droid or GitHub** (not Google Play). Optional: Termux:Widget, Termux:X11.
- Network access to Telegram, DeepSeek, Anthropic and OpenAI (in some regions this means a VPN/proxy app on the phone).
- Accounts: Telegram, DeepSeek API, Anthropic API, a ChatGPT plan that includes Codex. Optional: GitHub, Tavily, Notion.

## Quick start

Follow **[docs/SETUP.md](docs/SETUP.md)** step by step (an AI assistant can follow it too — see [AGENTS.md](AGENTS.md)). Daily use: **[docs/USAGE.md](docs/USAGE.md)**.

Secrets are typed on the phone with `scripts/set_secret.py` (hidden input, written to `.env` with mode 600). Never paste keys into a chat — not into Telegram, and not into your AI assistant.

## Project layout

```text
ai-council/            the bot (one codebase, three roles)
  agents/              deepseek/, claude/, codex/ (client + /code coder), tools.py, tool_gateway.py, files.py
  gateway/telegram/    bot.py: permissions, routing, commands, discussions, logging
  orchestrator/        council.py (memory), discussion.py (multi-bot discussions)
  scripts/             set_secret.py, find_chat_id.py, codex_bind_window.sh, receive_bot_token.py, tools_mcp.py
  tests/               offline tests (no keys, no network)
  start.sh, stop.sh, requirements.txt, .env.example
ai-hub/                console: ai_hub.py, web/, tests/, bots.example.json, AI-Bots widget, ensure.sh, ADDING_AI.md
extras/                optional: chinese-desktop/ (XFCE translations), claude-desktop/ (Claude in PRoot)
docs/                  SETUP, USAGE (English + 简体中文)
AGENTS.md              instructions for AI coding assistants
```

## License

License: to be chosen by the repo owner.
