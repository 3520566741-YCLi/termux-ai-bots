#!/usr/bin/env bash
# 第一次配置 AI Council（Mini PC）：逐个输入三个机器人的 Telegram Token 和 API Key。
# 输入时屏幕不显示内容；写进 ~/ai-council/runtime/<角色>/.env（权限 600，不进 Git）。
# 已经有值的会先问要不要覆盖，可以随时重新运行。
set -euo pipefail
HOME_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
PY="$HOME_DIR/.venv/bin/python"
SET="$HOME_DIR/ai-council/scripts/set_secret.py"
RT="$HOME_DIR/runtime"
export AI_TOOLS_DIR="$RT/tools"
umask 077

for r in deepseek claude codex tools; do mkdir -p "$RT/$r"; touch "$RT/$r/.env"; chmod 600 "$RT/$r/.env"; done
chmod 700 "$RT" "$RT"/*

setval() {  # 写普通（非机密）配置
  "$PY" - "$1" "$2" "$3" <<'EOF'
import sys
from dotenv import set_key
set_key(sys.argv[1], sys.argv[2], sys.argv[3], quote_mode='never')
EOF
}
getval() { "$PY" -c "import sys; from dotenv import dotenv_values; print(dotenv_values(sys.argv[1]).get(sys.argv[2]) or '')" "$1" "$2"; }

ask_default() {  # ask_default 提示 默认值 → 输出结果
  local answer; read -r -p "$1 [$2]: " answer; echo "${answer:-$2}"
}

echo "== AI Council 配置 =="
echo "开始前请确认：手机上的三个机器人已经停止（同一个 Token 两边同时运行会互相抢消息）。"
echo

# ---- 允许的聊天 ----
chats=$(getval "$RT/deepseek/.env" ALLOWED_CHAT_IDS)
echo "1) 允许使用的 Telegram 聊天 ID（群是 -100 开头的负数，私聊是正数，多个用逗号分隔）。"
echo "   不知道的话可以看手机 ~/ai-council/.env 里的 ALLOWED_CHAT_IDS。"
chats=$(ask_default "   ALLOWED_CHAT_IDS" "${chats:-}")
[ -n "$chats" ] || { echo "需要至少一个聊天 ID"; exit 1; }
"$PY" "$SET" --instance "$RT/deepseek" --role deepseek --chats="$chats" >/dev/null
"$PY" "$SET" --instance "$RT/claude" --role claude --copy-chats "$RT/deepseek/.env" >/dev/null
"$PY" "$SET" --instance "$RT/codex" --role codex --copy-chats "$RT/deepseek/.env" >/dev/null

# ---- 模型名（不写死在代码里）----
echo
echo "2) 模型名（直接回车用括号里的建议值，和手机上一致）"
ds_model=$(ask_default "   DEEPSEEK_MODEL" "$(getval "$RT/deepseek/.env" DEEPSEEK_MODEL | sed 's/^$/deepseek-flash/')")
cl_model=$(ask_default "   CLAUDE_MODEL" "$(getval "$RT/claude/.env" CLAUDE_MODEL | sed 's/^$/claude-sonnet-5-5/')")
setval "$RT/deepseek/.env" DEEPSEEK_MODEL "$ds_model"
setval "$RT/deepseek/.env" DEEPSEEK_BASE_URL "https://api.deepseek.com"
setval "$RT/claude/.env" CLAUDE_MODEL "$cl_model"

# ---- 密钥 ----
echo
echo "3) 密钥：每一项粘贴后回车，屏幕不会显示。会联网验证一次（免费接口）。"
echo "-- DeepSeek 机器人（主持人）的 Telegram Bot Token"
"$PY" "$SET" --instance "$RT/deepseek" --field TELEGRAM_BOT_TOKEN
echo "-- DeepSeek API Key"
"$PY" "$SET" --instance "$RT/deepseek" --field DEEPSEEK_API_KEY
echo "-- Claude 机器人的 Telegram Bot Token"
"$PY" "$SET" --instance "$RT/claude" --field TELEGRAM_BOT_TOKEN
echo "-- Anthropic API Key（Claude）"
"$PY" "$SET" --instance "$RT/claude" --field ANTHROPIC_API_KEY
echo "-- Codex 机器人的 Telegram Bot Token（Codex 本身用 ChatGPT 订阅登录，不需要 API Key）"
"$PY" "$SET" --instance "$RT/codex" --field TELEGRAM_BOT_TOKEN

# ---- 可选工具 ----
echo
read -r -p "4) 要配置可选工具吗（GitHub 只读、Tavily 搜索、Notion）？[y/N] " yn
if [ "${yn:-N}" = y ] || [ "${yn:-N}" = Y ]; then
  for f in GITHUB_TOKEN TAVILY_API_KEY NOTION_TOKEN NOTION_PARENT_PAGE_ID; do
    read -r -p "   配置 $f？[y/N] " one
    if [ "${one:-N}" = y ] || [ "${one:-N}" = Y ]; then "$PY" "$SET" --field "$f"; fi
  done
fi

echo
echo "配置完成。接下来："
echo "  council install     安装开机自启服务"
echo "  council start       启动"
echo "  council bind        绑定主人（在 Telegram 给 Codex 机器人发 /codexbind 绑定码）"
