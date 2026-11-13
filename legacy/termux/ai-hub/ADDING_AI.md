# 给 AI 中枢接入新的 AI

AI 中枢只负责“启动 / 停止 / 重启 / 看状态”，不关心机器人内部怎么调用模型。任何满足下面约定的程序都能出现在手机控制台里。

## 1. 机器人目录约定

放在 Termux 私有目录，例如 `~/ai-claude/`：

| 文件 | 要求 |
|---|---|
| `start.sh` | 后台启动（`nohup … &`），几秒内返回；重复执行不能开第二个实例。 |
| `stop.sh` | 让主进程正常退出（SIGTERM）。 |
| `logs/bot.pid` | 主进程 PID。中枢只认“PID 存活且工作目录就是本目录”的进程。 |
| `logs/bot.log` | 可选。行格式 `YYYY-MM-DD HH:MM:SS,mmm LEVEL 事件名 key=value…`，控制台显示最近事件。 |

可直接复制 `~/ai-council` 的 `start.sh`、`stop.sh` 和日志写法。日志里不要写提问正文、回复正文或任何密钥；`chat=`、`message=`、`username=`、`token=`、`key=` 字段在控制台会被自动隐藏，但仍应避免写入。

密钥只放在该目录的 `.env`（权限 600），不要写进 `bots.json`，也不要提交到 Git。

## 2. 登记到 `~/.config/ai-hub/bots.json`

```json
{
  "bots": [
    {
      "id": "deepseek",
      "name": "DeepSeek 文字助手",
      "dir": "~/ai-council",
      "description": "deepseek-flash · 一问一答",
      "channel": "Telegram 群",
      "accent": "#4d6bfe"
    },
    {
      "id": "claude",
      "name": "Claude 助手",
      "dir": "~/ai-claude",
      "description": "说明文字",
      "channel": "Telegram 群",
      "accent": "#d97757",
      "start": ["bash", "start.sh"],
      "stop": ["bash", "stop.sh"],
      "pid_file": "logs/bot.pid",
      "log_file": "logs/bot.log",
      "enabled": true
    }
  ]
}
```

- `id`：小写字母/数字/`-`/`_`，唯一。
- `autorestart`：默认 `true`，被系统杀掉会自动拉起（用户点“停止”的不会）。
- `pricing`：可选，用于估算当日费用，格式 `{"currency": "元", "peak": {"hit": 0.04, "miss": 2, "out": 8}, "offpeak": {...}, "peak_weekdays": [0,1,2,3,4], "peak_hours": [[9,12],[14,18]]}`（元/百万 token）；按次收费的网页搜索可加 `"search": 0.01`（每次价格），日志里写 `searches=`。机器人需在 `reply_sent` 日志里写 `tokens_in= tokens_out= cache_hit=`。
- `actions`：可选附加按钮，`[{"label": "开放 Codex 绑定（30 分钟）", "command": ["bash", "scripts/codex_bind_window.sh"]}]`，在该 AI 目录下执行（参数数组，不经 shell），控制台卡片上显示为按钮，结果取输出最后一行提示。
- 顶层可选 `storage`：`{"dir": "~/codex-work/files", "count_dirs": ["~/codex-work"], "quota_gb": 10, "warn_gb": 8}`，控制台“手机文件”区管理占用与删除。
- 顶层可选 `network_checks`：`[{"name": "Telegram", "url": "https://api.telegram.org"}]`，控制台和桌面面板显示连通性。
- `start`/`stop`/`pid_file`/`log_file`/`enabled` 可省略，默认值如上。
- `start`、`stop` 是参数数组，不经过 shell 解析。
- 改完 JSON 不用重启任何东西，控制台每 5 秒自动刷新。

## 3. 验证

```bash
ai-hub status            # 新 AI 应出现在列表里
ai-hub start claude
ai-hub status            # 显示“运行中”
ai-hub stop claude
```

然后点手机桌面的 **AI-Bots** 按钮，确认卡片和按钮都正常。

## 注意

- 同一个 Telegram Bot token 只能被一个程序轮询；新 AI 要用新的 Bot，或在同一个程序里分流。
- 真实 Telegram 消息和模型调用会产生费用，开发时先用模拟测试（参考 `ai-hub/tests`）。
- 控制台只监听 `127.0.0.1:8765`，需要请求头里的随机 token（`~/.config/ai-hub/token`），20 分钟无访问自动退出。
