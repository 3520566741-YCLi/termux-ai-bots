# ai-hub console / AI 中枢控制台

**English** | [简体中文](#简体中文)

Start, stop and restart each bot on the phone, with a local web console. Python standard library only. Setup: [../docs/SETUP.md](../docs/SETUP.md) step 8.

| File | Location on the phone |
|---|---|
| `ai_hub.py` | `~/.local/bin/ai-hub` (CLI + web server) |
| `web/` | `~/.local/share/ai-hub/web/` |
| `ADDING_AI.md` | `~/.local/share/ai-hub/ADDING_AI.md` |
| `bots.example.json` | `~/.config/ai-hub/bots.json` (your real list) |
| `ensure.sh` | `~/.local/share/ai-hub/ensure.sh` (for `termux-job-scheduler`) |
| `AI-Bots` | `~/.shortcuts/AI-Bots` (Termux:Widget button) |

- **Registry-driven**: adding an AI only means editing `bots.json`, see [ADDING_AI.md](ADDING_AI.md).
- **Watchdog**: `ai-hub watch` checks every 2 minutes and restarts only bots that should be running (`~/.cache/ai-hub/desired.json`), at most 5 times per hour per bot. A `termux-job-scheduler` job running `ensure.sh` every 15 minutes brings the watchdog back if it was killed. An `action.lock` file lock serialises the web server, watchdog and CLI.
- **Network**: HTTPS HEAD to `network_checks` (cached 60 s); Telegram network errors from the last 10 minutes are shown on each card.
- **Cost estimate**: sums today's `reply_sent` tokens with each bot's `pricing` (peak/off-peak, per-search price). Public holidays are not known, so it is an estimate.
- **Extra buttons**: `actions` in the registry (e.g. open the Codex bind window).
- **Phone files**: optional top-level `storage` section shows usage of `~/codex-work` and lets you delete files (two confirmations; git repos are cleaned with `git rm` + push).
- **Optional extras**: `ai-hub hud` prints Pango text for a desktop status panel; a `~/.cache/ai-hub/backup.json` written by your own backup script is shown in the network bar. Both are unused unless you set them up.
- Only processes whose PID file matches and whose working directory is the bot directory are touched.
- **Security**: listens on 127.0.0.1; the API needs the `X-AI-Hub-Token` header and a local Host; the token is passed in the URL fragment and removed from the address bar; no access log; exits after 20 minutes idle. A new token (mode 600) is generated every time the console starts, and before `ai-hub open` hands the token to the browser it asks `GET /api/hello?c=<random challenge>` for `HMAC-SHA256(token, challenge)` and checks it — if another program holds port 8765 the browser is not opened. All git commands run with `-c core.hooksPath=/dev/null -c core.fsmonitor=false`. Logs shown in the page hide chat/message/username/token/key fields and key-like strings.

```bash
python3 -m unittest discover -s tests -v    # fake "sleep" bots, no network, no models
```

## 简体中文

在手机上统一启动、停止、重启各个机器人，并提供本机网页控制台，只用 Python 标准库。安装见 [../docs/SETUP.zh-CN.md](../docs/SETUP.zh-CN.md) 第 8 步。新增 AI 只需改 `bots.json`（见 [ADDING_AI.md](ADDING_AI.md)）。守护每 2 分钟检查，只拉起“应该运行”的机器人，每小时最多 5 次；系统定时任务每 15 分钟运行 `ensure.sh` 把守护拉回。费用按 `pricing` 估算。控制台只监听 127.0.0.1，需要 token，20 分钟无访问自动退出；每次启动都生成新 token（权限 600），`ai-hub open` 交出带 token 的链接前先用随机挑战核对 HMAC，确认 8765 端口上是控制台本身，被别的程序占用时不打开浏览器。git 命令都带 `-c core.hooksPath=/dev/null -c core.fsmonitor=false`。
