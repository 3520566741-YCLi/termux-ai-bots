# Instructions for AI assistants

If you are an AI (Claude Code, Codex, or any other assistant) helping someone set this up:

1. **Follow [docs/SETUP.md](docs/SETUP.md) step by step**, in order ([简体中文](docs/SETUP.zh-CN.md) if the human prefers Chinese). Do not skip or reorder steps.
2. **Verify each step with the listed Check command** before moving on. If a check fails, use the Troubleshooting table in SETUP.md; do not improvise around a failed check.
3. **Secrets**: ask the human for each secret one at a time, and have them **type it into `scripts/set_secret.py` themselves** on the phone (it uses hidden input and writes `.env` with mode 600).
   - **Never ask the human to paste keys, tokens or passwords into the chat** with you, and never put a secret on a command line, in a file you write, in a commit, or in a log.
   - If a secret appears in the conversation anyway, tell the human to revoke it and create a new one.
4. Commands run in **Termux on the Android phone**. If you can only reach the phone through `adb`, prefer asking the human to run commands in Termux; the optional adb secret path is in the SETUP appendix.
5. Ask before anything that costs money or is hard to undo: creating API keys, starting discussions, `/code` pushes, deleting files, changing phone settings.
6. Code layout and conventions:
   - `ai-council/` is one codebase run as three instances (`~/ai-council` = DeepSeek, `~/ai-codex`, `~/ai-claude` with symlinks). Role comes from `BOT_ROLE` in each instance's `.env`.
   - Run tests before deploying changes: `cd ai-council && python -m unittest discover -s tests` and `cd ai-hub && python -m unittest discover -s tests`. Tests are offline and need no keys.
   - Logs must never contain message text or secrets; keep that property when editing.
