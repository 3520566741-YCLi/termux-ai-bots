# Setup guide

**English** | [简体中文](SETUP.zh-CN.md)

Numbered steps for a human or an AI assistant. Every step ends with a **Check** — do not move on until it passes. All commands run **in Termux on the phone** unless marked otherwise.

Ground rules for secrets (bot tokens, API keys):

- The human types each secret into `scripts/set_secret.py` on the phone (hidden input). It is written to a `.env` file with mode 600 and never printed.
- Never paste a secret into Telegram, into an AI chat, into a shell command line, or into a file that is committed to Git.
- If a secret leaks anyway, revoke it at the provider and create a new one.

Placeholders used below: `example_deepseek_bot`, `example_claude_bot`, `example_codex_bot` (your bots' usernames), `-1001234567890` (your group's chat ID).

---

## 1. Install the Android apps

Install from **F-Droid** (<https://f-droid.org>) or the apps' **GitHub releases** — not from Google Play (the Play builds are outdated and cannot share data with the add-ons). All Termux apps must come from the same source, because they must be signed with the same key.

- **Termux** — required.
- **Termux:API** — required (wake lock, job scheduler, notifications).
- **Termux:Widget** — optional, a home-screen button for the console.
- **Termux:X11** — optional, only for the desktop extras in `extras/`.

Open Termux once and let it finish its first-run setup.

**Check:** Termux opens to a shell prompt. (After step 3, `termux-battery-status` prints JSON, which proves Termux:API works.)

## 2. Keep the phone from killing Termux

Android, and Huawei / HarmonyOS phones in particular, kill background apps aggressively.

- Settings → Battery → App launch (or "Battery optimization") → Termux: **manage manually**, allow auto-launch, secondary launch and run in background. Do the same for Termux:API.
- Disable battery optimization for Termux; keep the phone on a charger.
- Pull down the Termux notification and tap **Acquire wakelock** (the bots also call `termux-wake-lock`).
- If you use a VPN/proxy app (e.g. Clash) to reach Telegram/OpenAI/Anthropic, apply the same settings to it and keep it always on.

**Check:** lock the phone for 10 minutes, unlock it, and Termux's notification is still there.

## 3. Install Termux packages

```bash
pkg update
pkg install -y python git proot termux-api openssh rsync
```

`proot` is required by Codex (see step 9). `openssh` is only needed for `/code` and file saving.

**Check:** `python --version && git --version && proot --version | head -n 1` prints three versions, and `termux-battery-status` prints JSON.

## 4. Get the code and create the Python environment

```bash
git clone https://github.com/3520566741-YCLi/termux-ai-bots.git ~/ai-bots-src
cp -r ~/ai-bots-src/ai-council ~/ai-council
chmod 700 ~/ai-council
cd ~/ai-council
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

**Check:** `cd ~/ai-council && .venv/bin/python -m unittest discover -s tests` ends with `OK` (offline; no keys needed).

## 5. Create three Telegram bots and a group

1. In Telegram, open **@BotFather** and send `/newbot` three times — one bot per AI, e.g. `example_deepseek_bot`, `example_claude_bot`, `example_codex_bot`. BotFather shows each bot's token; keep that chat open, you will type the tokens into the phone in step 7.
2. Create a group, add all three bots.
3. Make all three bots **administrators** of the group. By default ("privacy mode") a bot in a group only receives commands and messages that mention or reply to it; as admins they receive every message, so plain messages can go to DeepSeek. The routing code makes sure only one bot answers. (Alternative: `/setprivacy` → Disable in BotFather, then remove and re-add the bot.)
4. If you plan to change the group's settings (e.g. make history visible), do it now: converting a group to a **supergroup changes its chat ID**.

**Check:** the group member list shows the three bots with the admin badge.

## 6. Create the Codex and Claude instance directories

`~/ai-council` holds the real code (role `deepseek`). The other two instances get their own `.env`, `logs/` and `memory_store/`, and symlink everything else back to `~/ai-council`:

```bash
ac="$HOME/ai-council"

cx="$HOME/ai-codex"
mkdir -p "$cx/logs" "$cx/memory_store" "$ac/tools/codex-cloud/bin"; chmod 700 "$cx"
for f in agents gateway orchestrator config.py start.sh stop.sh scripts tests tools .venv requirements.txt; do ln -s "$ac/$f" "$cx/$f"; done

cl="$HOME/ai-claude"
mkdir -p "$cl/logs" "$cl/memory_store"; chmod 700 "$cl"
for f in agents gateway orchestrator config.py start.sh stop.sh scripts tests .venv requirements.txt; do ln -s "$ac/$f" "$cl/$f"; done
```

(`tools/` is linked only into the Codex instance; it holds the `codex` binary.)

**Check:** `ls -l ~/ai-codex ~/ai-claude` shows the symlinks pointing into `/data/data/com.termux/files/home/ai-council/`.

## 7. Enter bot tokens, API keys and chat IDs

`set_secret.py` asks for the value with hidden input, checks its format, writes it to the chosen instance's `.env` (mode 600) and runs a free verification call. Use the same Python for every instance:

```bash
S="$HOME/ai-council/.venv/bin/python $HOME/ai-council/scripts/set_secret.py"
```

**7a. DeepSeek instance** (`~/ai-council`). Create an API key at <https://platform.deepseek.com> first.

```bash
$S --instance ~/ai-council --field TELEGRAM_BOT_TOKEN --role deepseek   # type the token of example_deepseek_bot
$S --instance ~/ai-council --field DEEPSEEK_API_KEY
```

**7b. Find the chat IDs.** In the group send `/start@example_deepseek_bot`, and send any message to the bot in a private chat too. Then (with the bot *not* running):

```bash
~/ai-council/.venv/bin/python ~/ai-council/scripts/find_chat_id.py --instance ~/ai-council
```

It prints `chat-id  type  title`. Group IDs are negative (supergroups start with `-100`); your private chat ID is positive. Allow the group and, if you want, your private chat. **Use `=` with negative numbers:**

```bash
$S --instance ~/ai-council --chats=-1001234567890,123456789
```

**7c. Claude instance** (`~/ai-claude`). Create a key at <https://console.anthropic.com>.

```bash
$S --instance ~/ai-claude --field TELEGRAM_BOT_TOKEN --role claude --copy-chats ~/ai-council/.env
$S --instance ~/ai-claude --field ANTHROPIC_API_KEY
```

Optional: `CLAUDE_MODEL=...` in `~/ai-claude/.env` (default `claude-sonnet-5-5`).

**7d. Codex instance** (`~/ai-codex`) — no API key, it uses your ChatGPT login (step 9).

```bash
$S --instance ~/ai-codex --field TELEGRAM_BOT_TOKEN --role codex --copy-chats ~/ai-council/.env
```

Resulting `.env` contents (values hidden):

| Instance | Keys |
|---|---|
| `~/ai-council` | `BOT_ROLE=deepseek`, `TELEGRAM_BOT_TOKEN`, `ALLOWED_CHAT_IDS`, `DEEPSEEK_API_KEY`, optional `DEEPSEEK_MODEL` / `DEEPSEEK_BASE_URL` |
| `~/ai-claude` | `BOT_ROLE=claude`, `TELEGRAM_BOT_TOKEN`, `ALLOWED_CHAT_IDS`, `ANTHROPIC_API_KEY`, optional `CLAUDE_MODEL` |
| `~/ai-codex` | `BOT_ROLE=codex`, `TELEGRAM_BOT_TOKEN`, `ALLOWED_CHAT_IDS` |

**Check** (prints role and chat IDs, never secrets):

```bash
for d in ai-council ai-claude ai-codex; do (cd ~/$d && AI_BOT_ROOT=$PWD .venv/bin/python -c 'from config import load_config; c = load_config(); print(c.role, sorted(c.chats))'); done
stat -c '%a %n' ~/ai-council/.env ~/ai-claude/.env ~/ai-codex/.env    # all 600
```

## 8. Install the ai-hub console

```bash
src=~/ai-bots-src/ai-hub
install -Dm700 $src/ai_hub.py ~/.local/bin/ai-hub
mkdir -p ~/.local/share/ai-hub ~/.config/ai-hub && chmod 700 ~/.config/ai-hub
cp -r $src/web ~/.local/share/ai-hub/
install -m 600 $src/ADDING_AI.md ~/.local/share/ai-hub/
install -m 700 $src/ensure.sh ~/.local/share/ai-hub/ensure.sh
[ -f ~/.config/ai-hub/bots.json ] || install -m 600 $src/bots.example.json ~/.config/ai-hub/bots.json
mkdir -p ~/.shortcuts && install -m 700 $src/AI-Bots ~/.shortcuts/AI-Bots      # Termux:Widget button
```

Make sure `~/.local/bin` is on your `PATH` (add `export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc`, then open a new session).

Optional — let Android restart the watchdog every 15 minutes even if Termux was killed:

```bash
termux-job-scheduler --job-id 4242 --period-ms 900000 --script ~/.local/share/ai-hub/ensure.sh
```

The console creates a new random access token (file mode 600) every time it starts, so an old console link stops working after a restart. Before `ai-hub open` hands the token to the browser, it checks that the program on port 8765 really is this console (it asks for an HMAC of a random challenge); if another program holds the port, the browser is not opened and you get a warning.

**Check:** `ai-hub status` lists `deepseek`, `codex`, `claude` as stopped. Long-press the home screen → Widgets → Termux:Widget → add, tap **AI-Bots**: the console opens in the browser.

## 9. Install and log in to the Codex CLI

1. Open <https://github.com/openai/codex/releases/latest> and find the **aarch64 Linux musl** builds of `codex` and `codex-code-mode-host` (asset names contain `aarch64-unknown-linux-musl`; `codex-code-mode-host` is needed for `/code`). Download them into `~/ai-council/tools/codex-cloud/bin/`:

   ```bash
   cd ~/ai-council/tools/codex-cloud/bin
   curl -LO https://github.com/openai/codex/releases/download/<tag>/<codex-asset>.tar.gz
   curl -LO https://github.com/openai/codex/releases/download/<tag>/<code-mode-host-asset>.tar.gz
   ```

2. Verify SHA-256 against the release (GitHub shows a `sha256:` digest next to each asset; the API exposes it too):

   ```bash
   sha256sum *.tar.gz
   curl -s https://api.github.com/repos/openai/codex/releases/latest | ~/ai-council/.venv/bin/python -c 'import json,sys; [print(a.get("digest"), a["name"]) for a in json.load(sys.stdin)["assets"] if "aarch64-unknown-linux-musl" in a["name"]]'
   ```

   The two hashes must match exactly. If not, delete the files and stop.

3. Extract and name the binaries exactly `codex` and `codex-code-mode-host`:

   ```bash
   for f in *.tar.gz; do tar -xzf "$f"; done
   ls    # rename the extracted files, e.g.  mv codex-aarch64-unknown-linux-musl codex
   chmod 755 codex codex-code-mode-host
   rm -f *.tar.gz
   ```

4. Log in with your ChatGPT account. The musl binary looks for `/etc/resolv.conf`, which Android does not have, so DNS only works inside `proot` with Termux's resolv.conf bound in; it also needs Termux's CA bundle:

   ```bash
   cd ~/ai-council/tools/codex-cloud/bin
   CODEX_CA_CERTIFICATE=$PREFIX/etc/tls/cert.pem proot -b $PREFIX/etc/resolv.conf:/etc/resolv.conf ./codex -c 'cli_auth_credentials_store="file"' login
   ```

   Open the printed link in the phone's browser and sign in with ChatGPT. If the browser cannot reach the local callback, run the same command with `login --device-auth` and enter the code shown.

The bot code already does the rest: it runs `codex --search ... exec` inside `proot` with the same resolv.conf bind, hides `.env`, `~/.ssh`, `~/.config/ai-hub` and `~/.config/ai-tools` from Codex, and sets `default_tools_approval_mode="approve"` for its read-only GitHub MCP server (in `exec` mode MCP calls are otherwise auto-rejected).

**Check:** the same command with `login status` instead of `login` prints `Logged in using ChatGPT`.

## 10. Shared tools (optional): GitHub, Tavily, Notion

All four values go to `~/.config/ai-tools/.env` (directory 700, file 600) — `set_secret.py` picks that file automatically for these fields.

- **GitHub** — <https://github.com/settings/personal-access-tokens> → fine-grained token → Repository access: **All repositories** → Permissions: **Contents: Read-only** (Metadata read-only is added automatically). Nothing else.
- **Tavily** (DeepSeek's web search) — key from <https://app.tavily.com>.
- **Notion** (DeepSeek only) — <https://www.notion.so/profile/integrations> → new **internal** connection → capabilities **Read content, Update content, Insert content** (user information: none or without email). In the connection's **Content access** tab, pick the pages it may see. Create a page for saved notes (e.g. "AI Notes") and include it; its ID is the 32 hex characters at the end of its link — you can paste the whole link. DeepSeek gets Notion write tools only when **the owner's** message has a save intent ("save", "保存", "记到", "整理到", "Notion", "笔记" …); new pages are always created under `NOTION_PARENT_PAGE_ID`, appends only go to that page or its direct children, and nothing is ever deleted or rewritten.

```bash
$S --field GITHUB_TOKEN
$S --field TAVILY_API_KEY
$S --field NOTION_TOKEN
$S --field NOTION_PARENT_PAGE_ID
```

**Check:** each command reports the verification result (GitHub prints your account name; Notion confirms the page is reachable).

## 11. Start the bots

```bash
ai-hub start deepseek
ai-hub start claude
ai-hub start codex
ai-hub status
```

(Without ai-hub: `bash ~/ai-council/start.sh`, `bash ~/ai-claude/start.sh`, `bash ~/ai-codex/start.sh`.)

**Check:** each log has a `bot_ready` line:

```bash
for d in ai-council ai-claude ai-codex; do grep bot_ready ~/$d/logs/bot.log | tail -n 1; done
```

It shows the role, model, configured `tools=` and `sees_all_group_messages` (`True` only if privacy mode is off; admins see everything even when this says `False`).

## 12. Bind the Codex owner

Codex runs on your subscription and can read files on the phone, so only one Telegram user (you) may use it. Discussions also require this binding, and the same owner is the only user who gets tools and memory from DeepSeek and Claude.

```bash
bash ~/ai-codex/scripts/codex_bind_window.sh
```

(or tap **开放 Codex 绑定（30 分钟）** on the Codex card in the console). The script (or button) prints a one-time **6-digit code**. Within 30 minutes, send `/codexbind@example_codex_bot <code>` in the group (in a private chat `/codexbind <code>` is enough). A wrong code does not bind, 5 wrong codes close the window, and the code is void once binding succeeds.

**Check:** `~/ai-codex/codex_owner.json` exists, and the bot replied "已绑定". To change the owner later, delete that file and repeat.

## 13. Test in Telegram

1. In the group: `你好，用一句话介绍你自己` → only DeepSeek answers.
2. `@example_claude_bot 你好` → only Claude answers. `@example_codex_bot 你好` → only Codex answers (tens of seconds).
3. Send a photo with a caption → DeepSeek describes it.
4. `讨论 1轮 用一句话说说早睡的好处` → three short turns and a Codex summary.

**Check:** `grep reply_sent ~/ai-council/logs/bot.log | tail -n 3` shows token counts, and the console shows today's cost.

## 14. Optional: `/code` and saving files to GitHub (Codex)

`/code <task>` lets Codex edit a copy of one of your repositories on the phone, commit, and push a new `codex/<time>` branch (never `main`). Files you send to the Codex bot are stored on a `files` branch of the same repo.

> **`/code` is full code execution on the phone.** Treat every task — and everything Codex reads while working (repository content, files under `_files/`) — as code running as your Termux user. Only the bound owner can start it.

1. Create an SSH key for the phone and add it to that repository as a **deploy key with write access**:

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N '' -C 'termux-codex'
   cat ~/.ssh/id_ed25519.pub     # paste into GitHub → repo → Settings → Deploy keys (allow write)
   ssh -T git@github.com         # if port 22 is blocked, use ssh.github.com port 443 in ~/.ssh/config
   ```

2. Create `~/ai-codex/codex_code.json` (mode 600):

   ```json
   {"repo": "git@github.com:YOUR_USER/YOUR_REPO.git", "web": "https://github.com/YOUR_USER/YOUR_REPO", "dir": "~/codex-work/YOUR_REPO"}
   ```

3. For file saving, create an empty `files` branch once (e.g. `git switch --orphan files && git commit --allow-empty -m files && git push origin files`).

4. **Recommended:** protect `main` on GitHub (repo → Settings → Rules / Branches: require a pull request, block force pushes, and do not let deploy keys bypass the rule) so the phone's deploy key can only push `codex/*` branches (and `files`), even if the phone is compromised.

Android without root has no user namespaces, so Codex runs `/code` with `danger-full-access`. Mitigations:

- Every task starts from a **fresh clone** of `origin/main` (the old work directory is deleted), so hooks or git config planted by an earlier task do not survive.
- After Codex finishes, the bot never runs git in the directory Codex touched: it copies the files into a separate clean clone (skipping `.git` and `_files/`, copying symlinks as links without following them, skipping FIFOs/devices), scans the diff for secret-like strings, commits and pushes only the `codex/<time>` branch.
- Every git command of the bot and the console runs with `-c core.hooksPath=/dev/null -c core.fsmonitor=false`, so **git hooks and fsmonitor are disabled**.
- **Secrets are hidden via proot**: the three instances' `.env` and `memory_store/`, `~/.ssh` (Codex never sees the push key), `~/.config/ai-hub`, `~/.config/ai-tools` and `~/codex-work/files` are mounted empty. If `proot` is missing, `/code` refuses to run.
- Files changed in your home directory outside the repo are reported in the reply.

Remaining risks: this is not isolation. Codex can still reach the network, start background processes, modify any file that proot does not hide (including the bot's own code, `~/.bashrc`, `~/.gitconfig`, start scripts), and read its own ChatGPT login file; the after-the-fact scan only warns, it does not block.

**Check:** `/code add a line to README saying hello` → the reply contains a `compare/main...codex/...` link.

---

## Security notes

- **Only the bound owner gets tools and memory.** For the owner, DeepSeek and Claude use GitHub (including private repos), web search and Notion, and remember the conversation. Everyone else in an allowed chat gets plain answers without tools, their questions are not stored in (and cannot see) the memory, and each person is limited to **10 questions per hour** per chat. Still, only allow chats you trust: they spend your API balance.
- Notion writes happen only when the owner's message asks to save, and always under `NOTION_PARENT_PAGE_ID`.
- Codex is owner-only because it can run commands on the phone and its own login file cannot be hidden from it. `/code` is full code execution — see step 14 for the mitigations and remaining risks.
- `/codexbind` needs the 6-digit code printed by `scripts/codex_bind_window.sh`, so someone else in the group cannot claim the bot while the window is open.
- The bots log only event names and numbers (no message text, no keys). ai-hub additionally redacts chat/user IDs and anything key-like.
- The console listens on 127.0.0.1 only and requires a random token that is regenerated every time the console starts; before opening the browser it verifies that port 8765 is really served by the console. It exits after 20 minutes without visits.
- Restarting a bot drops queued Telegram messages on purpose (avoids paying twice).

## Updating

```bash
cd ~/ai-bots-src && git pull
rsync -a --exclude .env --exclude .venv --exclude logs --exclude memory_store --exclude tools \
      --exclude codex_owner.json --exclude codex_code.json ~/ai-bots-src/ai-council/ ~/ai-council/
cd ~/ai-council && .venv/bin/python -m unittest discover -s tests && for b in deepseek codex claude; do ai-hub restart $b; done
install -m 700 ~/ai-bots-src/ai-hub/ai_hub.py ~/.local/bin/ai-hub && cp -r ~/ai-bots-src/ai-hub/web ~/.local/share/ai-hub/
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Bots stop after a while, `bot.log` ends with network errors and no `bot_stopped` | Android killed Termux. Redo step 2 (Huawei: App launch → manage manually). Keep the watchdog job from step 8. If it still happens on Android 12+, read the Termux wiki on the "phantom process killer". |
| `bot_ready` never appears; `launcher.log` says `NetworkError` | No route to Telegram. Check Wi-Fi and your VPN/proxy (e.g. Clash). The bot keeps retrying and connects by itself once the network is back. ai-hub cannot detect a VPN on Huawei (no access to `/sys/class/net`). |
| In the group only commands/mentions work; plain messages are ignored | Privacy mode. Make the bots admins (step 5) or disable privacy in BotFather and re-add them. |
| Two bots answer the same message | Two instances have the same `BOT_ROLE` (only `deepseek` answers unaddressed messages). Run the check in step 7. |
| Bot ignores a group that used to work | The group became a supergroup and got a new ID. Run `find_chat_id.py` again and update `--chats=`. |
| `set_secret.py` says "wrong format" | You copied extra text or only part of the key. It already strips terminal paste markers (`ESC[200~`/`ESC[201~`). Copy the key again from the provider. |
| `--chats -100…` → "expected one argument" | Use `--chats=-100…` (negative numbers look like options). |
| Codex: "暂时不可用" / login works on a PC but not on the phone | DNS: Codex must run inside `proot -b $PREFIX/etc/resolv.conf:/etc/resolv.conf` (step 9). Also check `login status` and that `proot` is installed. |
| Codex cannot use GitHub tools | The MCP server needs `default_tools_approval_mode="approve"` (already set) and a `GITHUB_TOKEN` in `~/.config/ai-tools/.env`; restart the Codex bot after adding it. |
| `/code` fails with "codex_code.json" | Create it (step 14). Clone/push failures mean the deploy key is missing or lacks write access. |
| Discussion says Codex/Claude not configured | Their `.env` must load (`load_config` check in step 7) and the bots must be members of the group. Only the bound Codex owner can start a discussion. |
| Claude: "不便回答" | The request was refused by Anthropic's safety system even after the automatic fallback. Rephrase. |
| Typing secrets from a computer over adb | See the appendix below. |

## Appendix: send a secret from a computer over adb (optional)

If you prefer typing on a computer, `scripts/receive_bot_token.py` listens once on `127.0.0.1:38421` on the phone (10-minute timeout) and you forward the port over USB. It requires a one-time **nonce**: the sender must send `<nonce> <secret>` on one line. A wrong nonce, or anything that looks like an HTTP request (e.g. a web page in your computer's browser posting to `127.0.0.1:38421`), is rejected and nothing is saved.

On the computer, generate the nonce first:

```bash
openssl rand -hex 16        # e.g. 3f9c…  — use the same value on both sides
```

Phone (Termux), inside the instance directory:

```bash
cd ~/ai-claude && AI_BOT_ROOT=$PWD .venv/bin/python scripts/receive_bot_token.py --role claude --nonce <nonce> --field ANTHROPIC_API_KEY
```

Computer (USB debugging on), replacing `<nonce>` with the value above:

```bash
adb forward tcp:38421 tcp:38421
# zsh (macOS default) — note the  name?prompt  syntax; `read -p` means something else in zsh:
read -rs "T?Paste the secret, then Enter: " && printf '%s %s\n' <nonce> "$T" | nc 127.0.0.1 38421; unset T
# bash:
read -rsp 'Paste the secret, then Enter: ' T && printf '%s %s\n' <nonce> "$T" | nc 127.0.0.1 38421; unset T
adb forward --remove tcp:38421
```

`scripts/set_secret.py` (typing on the phone) does not use the network and needs no nonce.

macOS Terminal wraps pasted text in bracketed-paste markers; the receiver strips them and extracts the key. Shared tool keys go to `~/.config/ai-tools`: run the receiver with `AI_BOT_ROOT=~/.config/ai-tools`, `--role deepseek` (required but unused), `--nonce <nonce>` and `--field GITHUB_TOKEN` (or `TAVILY_API_KEY`, `NOTION_TOKEN`).
