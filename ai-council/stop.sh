#!/data/data/com.termux/files/usr/bin/bash
set -eu
cd "$(dirname "$0")"
if [ ! -f logs/bot.pid ]; then echo 'Bot 未运行。'; exit 0; fi
pid=$(cat logs/bot.pid)
case "$pid" in ''|*[!0-9]*) echo 'PID 文件无效。'; exit 1;; esac
if [ -r "/proc/$pid/cmdline" ] && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q 'gateway.telegram.bot'; then
  kill -TERM "$pid"
  echo '已请求 Bot 正常退出。'
else
  echo '没有找到对应的 Bot 进程。'
fi
