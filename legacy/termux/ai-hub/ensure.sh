#!/data/data/com.termux/files/usr/bin/sh
# 系统定时任务（termux-job-scheduler）调用：检查一次并确保 AI 守护在后台运行
# Called by termux-job-scheduler: run one check and make sure the ai-hub watchdog is running.
mkdir -p "$HOME/.cache/ai-hub"
exec "$HOME/.local/bin/ai-hub" ensure >> "$HOME/.cache/ai-hub/job.log" 2>&1
