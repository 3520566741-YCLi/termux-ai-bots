# 日常使用

[English](USAGE.md) | **简体中文**

把 `example_*_bot` 换成你的机器人用户名。

## 提问

| 场景 | DeepSeek / Claude | Codex（仅主人） |
|---|---|---|
| 私聊 | 直接发文字 | 直接发文字 |
| 群里普通消息 | DeepSeek 回答 | —— |
| 群里点名 | `@example_claude_bot 问题`、`/ask@example_claude_bot 问题`，或回复机器人 | `@example_codex_bot …`、`/ask@example_codex_bot …`，或回复 |
| 图片 | 发图片，说明文字就是问题（群里在说明里写 `/ask@机器人 …`，或回复机器人）。不写说明 = “描述图片并识别文字” | 同左，仅主人 |

- 记忆：每个聊天保留最近 10 轮问答（最多 16000 字），14 天不说话自动忘记，存在手机的 `memory_store/`。发 `/new@example_deepseek_bot`（其他机器人同理）立即清空。
- 限制：每个问题 4000 字以内；DeepSeek/Claude 同一聊天每 5 秒最多一问；图片支持 JPEG/PNG/GIF/WebP，最大 10 MB。
- Codex 一次只处理一个请求（超时 4 分钟），在后台运行，不影响其他机器人。
- 状态提示以 `⏳ …` 代码样式显示，答案发出后自动删除。

## 工具

- **联网搜索**：问需要最新信息的问题（“今天北京天气”“Python 最新版本”）—— 每个机器人用自己的搜索。
- **GitHub（只读）**：“列出我的仓库”“X 仓库的 README 写了什么”“在我的代码里搜 `load_config`”。
- **Notion（只有 DeepSeek）**：“在我的 Notion 里搜 …”“把这些保存到 Notion：…”“把这段追加到某页面”。DeepSeek 只新建和追加，不删除、不改写。默认存到 `NOTION_PARENT_PAGE_ID` 指定的页面下（也可以指定其他已共享的页面），完成后回复链接。
  - 讨论结束后，话题和 Codex 的综合总结会自动记进 DeepSeek 的对话记忆（停止或出错时记下已有发言），所以直接说“把刚才的讨论整理一下保存到 Notion”即可。

## 讨论（仅主人，在群里）

| 命令 | 含义 |
|---|---|
| `讨论 话题`（或 `讨论：话题`、`/discuss 话题`） | DeepSeek → Claude → Codex 轮流发言，默认 5 轮 |
| `讨论 3轮 话题`（也可写 `讨论 三轮 话题`、`讨论 3 话题`） | 自己设轮数，1–10 |
| `讨论 3轮 Claude DeepSeek：话题` | 只让点名的参加；名字后面必须有冒号 —— 没有冒号就当作话题的一部分 |
| `停止讨论`（或 `/discuss stop`） | 当前发言结束后停止 |

最后一定由 Codex 把所有方案结合起来做综合总结。Claude 没配置好时，不点名的讨论只用 DeepSeek 和 Codex。每个发言者都能看到此前全部发言（截短）。费用按各自机器人记账，控制台显示正确。

## Codex 附加功能（仅主人）

- `/code 任务` —— Codex 在 `codex_code.json` 指定的仓库副本里改代码、按要求跑测试，提交并推一个新的 `codex/<时间>` 分支，回复总结、`--stat` 和对比链接。绝不推 `main`。超时 15 分钟。需要先完成安装第 14 步。
- 给 Codex 机器人发**文件** → 存到仓库 `files` 分支的 `YYYY-MM-DD/文件名`，回复 GitHub 链接（≤ 20 MB；含疑似密钥的文本文件会被拒绝）。**图片**配说明 `/save` 也这样保存。`/code` 任务里在 `_files/` 下能用到这些文件。
- 手机上 `~/codex-work` 最多占用 10 GB，可在控制台的“手机文件”区管理。

## ai-hub 控制台

| 命令 | 作用 |
|---|---|
| `ai-hub` / `ai-hub open`（或点 **AI-Bots** 小组件） | 启动本机控制台并在浏览器打开 |
| `ai-hub status` | 列出机器人和运行状态 |
| `ai-hub start\|stop\|restart <id>` | 控制某个机器人（`deepseek`、`codex`、`claude`） |
| `ai-hub watch` | 前台运行守护（每 2 分钟检查） |
| `ai-hub ensure` | 检查一次并确保守护在运行（定时任务调用） |
| `ai-hub network` | 检测 Telegram / DeepSeek / Anthropic 是否连得上 |
| `ai-hub menu` | 网页打不开时用的 `termux-dialog` 小菜单 |

页面显示每个机器人的状态、运行时长、今日提问/回复数、估算费用（来自 `~/.config/ai-hub/bots.json` 的 `pricing`）、最近日志（已脱敏）、网络状态，以及 **开放 Codex 绑定（30 分钟）** 这类附加按钮。守护只拉起你最后一次“启动”的机器人（点了“停止”的不会被拉起），同一个每小时最多 5 次。

给控制台接入新的 AI，见 [ai-hub/ADDING_AI.md](../ai-hub/ADDING_AI.md)。

## 日志

```bash
tail -n 20 ~/ai-council/logs/bot.log     # bot_ready、request、reply_sent tokens_in=… tokens_out=…
tail -n 20 ~/ai-codex/logs/bot.log
cat ~/.cache/ai-hub/restarts.log          # 自动重启记录
```

日志里从不记录消息正文和密钥。
