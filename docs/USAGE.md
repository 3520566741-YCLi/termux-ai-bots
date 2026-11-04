# Daily use

**English** | [简体中文](USAGE.zh-CN.md)

Replace `example_*_bot` with your bots' usernames.

## Asking questions

| Where | DeepSeek / Claude | Codex (owner only) |
|---|---|---|
| Private chat | just type | just type |
| Group, plain message | DeepSeek answers | — |
| Group, addressed | `@example_claude_bot question`, `/ask@example_claude_bot question`, or reply to the bot | `@example_codex_bot …`, `/ask@example_codex_bot …`, or reply |
| Photo | send a photo; the caption is the question (in a group put `/ask@bot …` in the caption, or reply to the bot). No caption = "describe and read the text" | same; owner only |

- **Owner vs. others**: only the bound owner (step 12 of SETUP) gets tools (GitHub, web search, Notion) and memory. Everyone else gets plain answers without tools; their questions are not remembered and they cannot see the memory; each person may ask at most 10 questions per hour per chat.
- Memory (owner only): each chat keeps the last 10 Q&A turns (up to 16,000 characters) for 14 days, stored on the phone in `memory_store/`. `/new@example_deepseek_bot` (or the same for the other bots) clears it.
- Limits: 4,000 characters per question; DeepSeek/Claude accept one question per chat every 5 seconds; images are JPEG/PNG/GIF/WebP up to 10 MB.
- Codex handles one request at a time (timeout 4 minutes) and runs in the background, so other bots stay responsive.
- Status messages appear as `⏳ …` in code style and are deleted once the answer arrives.

## Tools

- **Web search**: ask for something current ("今天北京天气", "latest Python release") — each bot uses its own search.
- **GitHub (read-only)**: "list my repositories", "what does README in repo X say", "search my code for `load_config`".
Tools are for the owner only.

- **Notion (DeepSeek only)**: "search my Notion for …", "save this to Notion: …", "append these notes to page …". DeepSeek only creates pages and appends; it never deletes or rewrites. Write tools are offered only when your message has a save intent (save, 保存, 存到, 记到, 整理到, 写进, Notion, 笔记 …). New pages are always created under `NOTION_PARENT_PAGE_ID`, and appends only go to that page or its direct sub-pages. It replies with the link.
  - When a discussion ends, its topic and Codex's combined summary are added to DeepSeek's chat memory (if it was stopped or failed, the turns so far are kept instead), so you can simply say "整理一下刚才的讨论，保存到 Notion".

## Discussions (owner only, in the group)

| Command | Meaning |
|---|---|
| `讨论 话题` (or `讨论：话题`, `/discuss 话题`) | DeepSeek → Claude → Codex take turns, 5 rounds by default |
| `讨论 3轮 话题` (also `讨论 三轮 话题`, `讨论 3 话题`) | choose the number of rounds, 1–10 |
| `讨论 3轮 Claude DeepSeek：话题` | only the named bots speak; the colon after the names is required — without it the names are part of the topic |
| `停止讨论` (or `/discuss stop`) | stop after the current speaker |

Codex always ends with a combined summary of all proposals. If Claude is not configured, an un-named discussion runs with DeepSeek and Codex only. Every speaker sees all earlier turns (truncated). Costs are logged per bot, so the console shows them correctly.

## Codex extras (owner only)

- `/codexbind <code>` — bind yourself as owner: run `bash ~/ai-codex/scripts/codex_bind_window.sh` (or tap **开放 Codex 绑定（30 分钟）**) and send the 6-digit code it prints within 30 minutes (in a group: `/codexbind@example_codex_bot <code>`).
- `/code <task>` — Codex edits a fresh clone of the repo from `codex_code.json`, runs tests if asked, commits and pushes a new `codex/<time>` branch, and replies with a summary, `--stat` and a compare link. It never pushes `main`. Timeout 15 minutes. Needs setup step 14. **This is full code execution on the phone**: git hooks are disabled and secrets are hidden via proot, but Codex can still use the network and change files proot does not hide — see SETUP step 14 for the remaining risks.
- Send a **file** to the Codex bot → stored on the repo's `files` branch as `YYYY-MM-DD/name`, reply with a GitHub link (≤ 20 MB; text files containing secret-like strings are refused). A **photo** with caption `/save` is stored the same way. `/code` tasks see these files under `_files/`.
- Phone storage for `~/codex-work` is capped at 10 GB; manage it in the console's file section.

## ai-hub console

| Command | What it does |
|---|---|
| `ai-hub` / `ai-hub open` (or the **AI-Bots** widget) | start the local console and open it in the browser |
| `ai-hub status` | list bots and whether they run |
| `ai-hub start\|stop\|restart <id>` | control one bot (`deepseek`, `codex`, `claude`) |
| `ai-hub watch` | run the watchdog in the foreground (checks every 2 minutes) |
| `ai-hub ensure` | one check + make sure the watchdog is running (used by the job scheduler) |
| `ai-hub network` | probe Telegram / DeepSeek / Anthropic reachability |
| `ai-hub menu` | small `termux-dialog` menu if the browser page does not open |

Each time the console starts it creates a new access token, and `ai-hub open` first verifies that port 8765 really is the console before opening the browser; if another program holds the port it warns instead.

The page shows each bot's state, uptime, today's questions/answers, estimated cost (from `pricing` in `~/.config/ai-hub/bots.json`; Codex runs on your ChatGPT subscription, so its figure is an API-price estimate of the tokens it used, shown with `note`), recent redacted log lines, network status and extra buttons such as **开放 Codex 绑定（30 分钟）** (its result line shows the 6-digit code for `/codexbind`). The watchdog only restarts bots you last started (a bot you stopped stays stopped) and at most 5 times per hour.

To add another AI to the console, see [ai-hub/ADDING_AI.md](../ai-hub/ADDING_AI.md).

## Logs

```bash
tail -n 20 ~/ai-council/logs/bot.log     # bot_ready, request, reply_sent tokens_in=… tokens_out=…
tail -n 20 ~/ai-codex/logs/bot.log
cat ~/.cache/ai-hub/restarts.log          # automatic restarts
```

Logs never contain message text or keys.
