# Termux（安卓手机）旧实现

2026-11 起正式运行环境迁移到 Mini PC（Ubuntu + systemd），见仓库根目录 README。
这里保留手机版的启动脚本、ai-hub 控制台、桌面扩展和安装文档，没有删除：

- 手机作为**冷备份**：平时手机上的机器人保持停止；Mini PC 出问题时在手机 ai-hub 里启动即可临时接管。
  Mini PC 检测到同一个 Token 在别处运行（Telegram 409 Conflict）会自动让位并在群里通知，
  回家后先停手机，再在 Mini PC 上执行 `council resume`。
- 手机上跑的是迁移前的代码：能问答、能用“讨论 3轮 话题”，没有 /debate、FINAL、SQLite 等新功能。

| 路径 | 原用途 |
|---|---|
| `ai-council/start.sh`、`stop.sh` | Termux 后台启动（wake lock、PID 文件） |
| `ai-hub/` | 手机网页控制台、守护进程（termux-dialog、termux-job-scheduler） |
| `extras/` | Termux 桌面汉化、proot 里的 Claude 桌面版 |
| `docs/` | 手机安装与使用文档 |

已从主代码里去掉的手机专属依赖：termux-wake-lock / unlock、Termux 证书路径（只在存在时使用）、
proot（Linux 上改用 bubblewrap；没有 proot 时 Codex 问答照常工作）。`/code`（让 Codex 改代码并推分支）
仍依赖 proot 隔离，在 Mini PC 上默认不可用，需要重新设计隔离方式后再开启。
