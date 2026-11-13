# AI Council · Mini PC

一个 Telegram 群里的三个 AI：**Claude**、**DeepSeek**、**Codex**。Mini PC（Ubuntu）24/7 运行，iPhone 在群里发问题，
三个模型按你指定的轮数讨论，最后一轮强制 FINAL，给出统一、可执行的最终方案。

手机（Termux）旧实现保留在 [legacy/termux/](legacy/termux/MIGRATION.md)，现在作为冷备份。

```text
iPhone ─ Telegram 群 ─┐ 长轮询（只有出站 HTTPS，不开端口）
                      ▼
┌──────────── Mini PC · systemd 用户服务（开机自启，无需登录桌面）────────────┐
│ ai-council.service         DeepSeek 机器人 + 讨论引擎（队列、SQLite）       │
│ ai-council-claude.service  Claude 机器人（被 @ 时回答；讨论里用它的账号）   │
│ ai-council-codex.service   Codex 机器人（codex CLI + ChatGPT 订阅）         │
│ ai-council-monitor.timer   每 5 分钟检查服务和心跳，自动重启 + 告警          │
│ ai-council-report.timer    每天 22:00 用量日报                              │
│ ai-council-backup.timer    每天 04:30 备份                                  │
│ ai-council-hub.service     状态网页（只在 Tailscale 内网可访问）            │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 目录

| 路径 | 内容 |
|---|---|
| `ai-council/` | 机器人代码：`gateway/telegram`（命令、路由、日志、看门狗）、`orchestrator`（讨论引擎 `debate.py`、SQLite `store.py`、自然语言识别 `intent.py`）、`agents`（DeepSeek / Claude / Codex 适配器、重试、工具） |
| `deploy/systemd/` | systemd 单元（`council install` 链接到 `~/.config/systemd/user/`） |
| `scripts/` | `council`（管理命令）、`setup_secrets.sh`、`monitor.py`（监控 / 日报）、`hub.py`（状态网页） |
| `runtime/<deepseek\|claude\|codex>/.env` | 每个机器人的配置和密钥（权限 600，**不进 Git**） |
| `runtime/tools/.env` | 可选工具密钥：GitHub、Tavily、Notion |
| `data/council.db` | SQLite：讨论、每轮输出、Council State、FINAL、设置、模型状态 |
| `logs/<角色>/app.log`、`error.log` | 日志（自动轮转：5 MB × 5、2 MB × 5） |
| `tools/codex/` | Codex CLI（GitHub Release，已校验 SHA256） |
| `backups/` | 每日备份（保留 14 天，含密钥，权限 700） |

## 部署（新机器从零开始）

```bash
sudo apt install -y python3.12-venv python3-pip gh git      # 一次性
gh auth login
git clone https://github.com/3520566741-YCLi/termux-ai-bots.git ~/ai-council
cd ~/ai-council && git checkout migration/minipc            # 合并后改用 main
python3 -m venv .venv && .venv/bin/pip install -r ai-council/requirements.txt
mkdir -p tools/codex/pkg && gh release download -R openai/codex \
  -p 'codex-package-x86_64-unknown-linux-musl.tar.gz' -O - | tar xz -C tools/codex/pkg
scripts/council test          # 离线测试（不需要密钥）
scripts/council setup         # 输入 Token / API Key（输入不显示）
scripts/council codex-login   # ChatGPT 设备码登录
scripts/council install       # 安装开机自启服务和定时器
council start
council bind                  # 显示绑定码，在 Telegram 给 Codex 机器人发 /codexbind 绑定码
```

需要 `loginctl enable-linger $USER`（本机已开启），用户服务才会在没人登录桌面时开机运行。

## 日常管理

| 命令 | 作用 |
|---|---|
| `council start` / `stop` / `restart` | 启动 / 停止 / 重启三个机器人 |
| `council status` | 服务状态、心跳、今日用量 |
| `council logs [deepseek\|claude\|codex] [-f]` | 看日志 |
| `council errors` | 最近错误 + systemd 日志 |
| `council resume` | 手机备份结束后让 Mini PC 重新接管 |
| `council hub-url` | 状态网页链接 |
| `council update` | 拉最新代码 → 装依赖 → 测试通过才重启 |

也可以直接用 systemd：`systemctl --user status ai-council ai-council-claude ai-council-codex`，
日志 `journalctl --user -u ai-council -f`。

**自动重启**：进程崩溃 5 秒后拉起（不限次数）；事件循环卡住 90 秒没有心跳，systemd 强制重启；
监控每 5 分钟检查一次服务和心跳，异常先自动重启，再在群里告警，恢复后通知。

## Telegram 命令（只有绑定的主人能发起和控制讨论）

| 命令 | 作用 |
|---|---|
| `/debate 3 问题` | 3 轮讨论，第 3 轮是 FINAL（不写轮数用默认轮数） |
| `/rounds 4` | 设置这个聊天的默认轮数（1–10，默认 3） |
| `/more 2` | 给进行中的讨论追加 2 轮；已结束的讨论从上次状态再继续 2 轮并重新 FINAL |
| `/final` | 当前这一轮结束后直接进入 FINAL |
| `/status` | 讨论进度、排队、三个模型在线状态、今日用量 |
| `/cancel` 或“停止讨论” | 取消当前讨论 |
| `/mode compact` | 默认。每轮只发：轮数、摘要、当前共识、当前争议；FINAL 发完整方案 |
| `/mode verbose` | 额外用各模型自己的机器人发完整发言 |

普通消息也能发起：“帮我讨论一下这个方案，4轮。”（没写轮数用默认轮数；回复某条消息说“帮我讨论一下”会把那条消息一起带上）。
旧口令“讨论 3轮 话题”“讨论 3轮 Claude DeepSeek：话题”仍然可用。
群里普通消息由 DeepSeek 回答；@ 或回复 Claude / Codex 时由它回答；`/new` 清空对话记忆。

### 讨论怎么进行

1. **第 1 轮**：三个模型并行、独立分析（互相看不到），允许联网搜索 / 读 GitHub。
2. **中间轮**：每个模型只拿到 Council State（原问题、当前共识、分歧、证据、被否决的想法、开放问题、上一轮摘要、上一轮各方要点），
   任务是反驳、修正、补充、找风险、比较方案、推动收敛。每轮后由 DeepSeek 把发言整理成新的 Council State（便宜）。
3. **FINAL**：各模型给最终立场，不许再制造新争议；Claude 写出 8 段式最终方案：最终结论、推荐方案、推荐原因、三方主要共识、
   仍存在的关键分歧、风险、下一步行动、备选方案。末尾附本次 token 用量。

完整历史只存数据库，不每轮塞回模型，控制 API 成本。某个模型失败只跳过它；全部失败才中止。
同一时间只跑一场讨论，其余排队；每个聊天同时最多一场；同一条消息不会重复执行；Codex 全机同时只跑一个子进程。
服务重启后，未完成的讨论从下一轮自动继续。

## 模型配置

在 `runtime/<角色>/.env`（`council setup` 会问）：

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL` | DeepSeek（主持实例） |
| `ANTHROPIC_API_KEY`、`CLAUDE_MODEL` | Claude 实例。模型名必填，代码不写死 |
| `TELEGRAM_BOT_TOKEN`、`ALLOWED_CHAT_IDS`、`BOT_ROLE` | 每个实例各一份 |
| `ALERT_CHAT_IDS` | 可选：告警和日报发到哪里（默认第一个群） |
| 环境变量 `COUNCIL_DEFAULT_ROUNDS` | 默认轮数（默认 3） |

DeepSeek / Claude 调用：超时 45 秒；网络错误、429、5xx 自动重试 3 次（遵守 Retry-After）；key 错误或余额不足不重试。
日报里的估算价格可在 `runtime/pricing.json` 修改，例如 `{"Claude": {"currency": "$", "in": 3, "out": 15}}`（每百万 token）。

## Codex 登录与恢复

Codex 用 ChatGPT 订阅（不走 OpenAI API）。登录凭据在 `~/.codex/auth.json`（文件存储，服务不需要桌面钥匙串）。

- 登录 / 重新登录：`council codex-login`，在手机浏览器打开显示的链接、输入验证码。
  如提示需要开启“设备代码登录”，先在 ChatGPT 设置 → 安全里打开。
- 检查：`council codex-status`。
- 登录失效时：Codex 被标记为离线并在群里提醒；Claude 和 DeepSeek 照常讨论；重新登录后 10 分钟内自动恢复（或 `council restart`）。

隔离：Codex 以只读沙箱、空临时目录、`--ephemeral` 运行。本机 Ubuntu 24.04 默认限制非特权用户命名空间，
bubblewrap 无法使用，所以外层隔离和 Codex 自带的沙箱目前都不完整；Codex 只为绑定的主人服务。
`/code`（让 Codex 改代码推分支）依赖 proot，在 Mini PC 上默认不可用。

## 手机备份（主备切换）

- 平时手机上的机器人保持停止（同一个 Token 两边同时运行会互相抢消息）。
- Mini PC 出问题而你不在家：在手机 ai-hub 里启动机器人即可。Mini PC 一旦检测到 Telegram 409 Conflict，会自动让位：
  写 `data/standby.json`、在群里通知、三个机器人退出且不自动重启。
- 回家后：先在手机上停掉机器人，再执行 `council resume`（或状态网页上的“恢复接管”）。

## 状态网页

`council hub-url` 显示链接（只监听 Tailscale 地址，外网打不开）。iPhone 打开 Tailscale 后访问一次带口令的链接，
之后浏览器会记住 30 天。可以看三个机器人和模型状态、最近讨论、今日用量和花费、最近事件；可以全部启动 / 停止 / 重启、单个重启、恢复接管。

## 日志与隐私

- `logs/<角色>/app.log`：事件和数字（debate id、模型、轮次、耗时、成功 / 失败、token 用量、错误类型），**不记聊天内容**。
- `logs/<角色>/error.log`：警告和错误。
- 落盘前会把 Telegram Bot Token、API Key 等替换成 `[REDACTED]`；第三方库日志（URL 里带 Token）不落盘。

## 备份与恢复

- 自动：每天 04:30 `council backup` → `backups/ai-council-时间.tar.gz`（SQLite 在线备份 + `runtime/` 配置和对话记忆），保留 14 天。
  备份里有密钥，请勿外传；需要异地备份时自己加密后再拷走。Codex 登录不在备份里（重新 `council codex-login` 即可）。
- 恢复：`council restore backups/ai-council-时间.tar.gz`（先自动备份当前状态，停服务，恢复，再启动）。

## 给 AI 助手的说明

改代码前后都运行 `council test`（离线，不需要密钥和网络）。日志不得包含消息正文或密钥；
不要让人把密钥贴进聊天，用 `council setup` 输入；不要把 `runtime/`、`data/`、`logs/`、`backups/` 提交到 Git。
