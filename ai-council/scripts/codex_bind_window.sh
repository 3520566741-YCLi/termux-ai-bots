#!/data/data/com.termux/files/usr/bin/bash
# 开放 30 分钟的 Codex 主人（owner）绑定窗口，并生成一次性 6 位绑定码；之后在 Telegram 发 /codexbind@Bot用户名 绑定码。
# Opens a 30-minute /codexbind window and prints a one-time 6-digit code. Run it from the Codex instance:
#   bash ~/ai-codex/scripts/codex_bind_window.sh   then send: /codexbind@your_codex_bot <code>
# 码不对不会绑定（输错 5 次窗口自动关闭），绑定成功后窗口文件删除、码作废。
# A wrong code never binds (5 wrong tries close the window); a successful bind deletes the window file.
# 已绑定时先删除 ~/ai-codex/codex_owner.json 才能换人。
set -eu
cd "$(dirname "$0")/.."
umask 077
# 从 /dev/urandom 取随机数生成 000000–999999 的码（取模偏差可以忽略）
code=$(printf '%06d' $(( $(od -An -N4 -tu4 /dev/urandom | tr -d ' ') % 1000000 )))
expires=$(( $(date +%s) + 30 * 60 ))
tmp=codex_bind_window.tmp
printf '{"code": "%s", "expires": %s}\n' "$code" "$expires" > "$tmp"
mv -f "$tmp" codex_bind_window
# AI 中枢按钮只显示输出的最后一行，绑定码放在最后一行 / the AI hub button shows only the last line
echo "已开放 30 分钟：请在 Telegram 发送 /codexbind@你的Bot用户名 $code / Window open for 30 min: send /codexbind@your_codex_bot $code"
