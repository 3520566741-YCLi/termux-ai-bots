#!/data/data/com.termux/files/usr/bin/bash
# 开放 30 分钟的 Codex 主人（owner）绑定窗口；之后在 Telegram 群里发 /codexbind@Bot用户名。
# Opens a 30-minute window for /codexbind. Run it from the Codex instance: bash ~/ai-codex/scripts/codex_bind_window.sh
# 已绑定时先删除 ~/ai-codex/codex_owner.json 才能换人。
set -eu
cd "$(dirname "$0")/.."
umask 077
echo $(( $(date +%s) + 30 * 60 )) > codex_bind_window
echo '已开放 30 分钟：请在 Telegram 群里发送 /codexbind@你的Bot用户名 / Window open for 30 min: send /codexbind@your_codex_bot'
