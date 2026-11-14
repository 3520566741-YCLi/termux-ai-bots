#!/data/data/com.termux/files/usr/bin/bash
set -eu
cd "$(dirname "$0")"
export AI_BOT_ROOT="$(pwd)"   # 同一套代码多个实例（ai-council / ai-codex）各用自己的目录
umask 077
.venv/bin/python -c 'from config import load_config; load_config()' 2>/dev/null || { echo '请先正确填写 .env。'; exit 1; }
mkdir -p logs
if [ -f logs/bot.pid ]; then
  pid=$(cat logs/bot.pid)
  case "$pid" in ''|*[!0-9]*) pid=invalid;; esac
  if [ -r "/proc/$pid/cmdline" ] && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q 'gateway.telegram.bot'; then
    echo 'Bot 已在运行。'
    exit 0
  fi
fi
if command -v termux-wake-lock >/dev/null; then termux-wake-lock; fi
nohup .venv/bin/python -m gateway.telegram.bot >> logs/launcher.log 2>&1 < /dev/null &
sleep 2
if [ -f logs/bot.pid ] && kill -0 "$(cat logs/bot.pid)" 2>/dev/null; then
  echo "Bot 已启动，PID $(cat logs/bot.pid)；日志：logs/bot.log"
else
  if command -v termux-wake-unlock >/dev/null; then termux-wake-unlock; fi
  echo '启动失败，请检查 logs/launcher.log。'
  exit 1
fi
