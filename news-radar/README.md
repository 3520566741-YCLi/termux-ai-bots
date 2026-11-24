# Tidings（24 小时私人资讯助手）

> 旧名「资讯雷达」。代码目录、服务名（news-radar*.service）和数据目录（data/radar）保持原名不变，只改显示名。

24 小时抓股票和加密货币快讯，DeepSeek 打 1~5 分，重要的用 **ntfy** 推到 iPhone；手机网页能看信息流、日报、聊天改关注、
生成 Telegram 讨论文案、把资料存进 Notion。只是提醒工具，不是投资建议；小道消息经常是假的。

## 组成

| 部分 | 说明 |
|---|---|
| `news-radar.service` | 抓取 → 去重 → 打分 → 推送前检查 → ntfy 推送；每天三期日报（北京时间 07:30 / 12:30 / 21:00，DeepSeek 半价时段） |
| `news-radar-web.service` | 手机网页后端，只监听 `127.0.0.1:8790`，用 `tailscale serve` 转成 HTTPS |
| `web/` | 手机网页（PWA，苹果液态玻璃风格），接口见 [web/API.md](web/API.md) |

推送只用 ntfy（+ 可选的网页推送）。Telegram 只在两处出现：抓取公开频道（来源，只读），以及网页上**手动点“发送到群”**时用专用机器人发讨论文案（绝不自动发）。

## 数据保留

- 信息流只是缓存：没收藏的消息，北京时间过了当天就只剩**标题、来源、原网页链接**（正文、摘要、配图删掉），30 天后整条删掉；
- 点「收藏」的完整图文永久留在网页的「收藏」里；真正要长期保存的再一键存进 Notion；
- 配图缓存在 `data/radar/img/`（缩到宽 900，单张最多 4MB），没人用的图自动删掉。

## 代码

- `radar/engine.py` 主循环；`sources.py` 来源；`scorer.py` 打分和推送前检查；`notify.py` 推送；`store.py` SQLite
- `radar/digest.py` 日报（参考 Deck 的 Vesper 日报：一期一次 DeepSeek 调用，只能引用候选编号，附口播稿）
- `radar/web.py` 网页后端；`assistant.py` 聊天（函数调用改关注/设置/查消息/存 Notion）；`draft.py` 讨论文案和发群；`notion.py` 存 Notion
- `radar/images.py` 配图（RSS/Telegram 图或原文 og:image，防内网地址，缓存缩小）；`memory.py` Vesper 长期记忆中心（和 Mac 同步）
- `radar/llm.py` 网页、日报、文案共用的 DeepSeek 调用，每次都按用途记账（usage 表）

## 安装（Mini PC，不需要 sudo）

```bash
git clone -b feature/news-radar <仓库地址> ~/news-radar-app     # 和 ~/ai-council 分开，不影响机器人
bash ~/news-radar-app/news-radar/deploy/install.sh               # 备份 → 独立 venv → 离线测试 → 装服务（默认试运行）
```

- 数据 `~/ai-council/data/radar/`，配置 `~/ai-council/runtime/radar/.env`（600），日志 `~/ai-council/logs/radar/`；都不进 Git。
- DeepSeek Key 只读 `~/ai-council/runtime/deepseek/.env`，不读别的机器人 Token。
- 试运行确认没问题后：删掉 `runtime/radar/.env` 里的 `RADAR_DRY_RUN=1`，`systemctl --user restart news-radar`。

### 手机网页

```bash
sudo tailscale set --operator=$USER            # 只要一次（用户本人运行）
tailscale serve --bg --https=8443 http://127.0.0.1:8790
cd ~/news-radar-app/news-radar && ~/.local/share/news-radar/venv/bin/python -m radar login-code   # 6 位一次性登录码（10 分钟有效）
```
手机打开 https://<机器名>:8443/ 输入登录码即可；也可以用 `web-url` 拿带口令的长链接。
iPhone Safari 打开链接 → 分享 → 添加到主屏幕。网页推送要从主屏幕图标打开后再开。

### ntfy

`python -m radar ntfy-topic` 显示订阅地址，iPhone 装 ntfy App 订阅。主题名相当于密码。

### Notion

1. notion.so/profile/integrations 新建“内部集成”，复制 Token；
2. 存档数据库右上角 ··· → 连接 → 选这个集成；
3. 网页“我的 → Notion”填 Token 和数据库链接。能识别的列：标题、类型（单选）、标的（多选）、分数（数字）、链接（网址）、来源、时间（日期）、传闻（勾选）。

### 讨论文案发到 Telegram 群（可选）

BotFather 新建专用机器人 → 拉进讨论群 → 在群里发一句话 → 网页“我的 → Telegram”填 Token、选群。

## 命令

```
python -m radar run | probe | test-push | ntfy-topic | web | web-url | demo
```
`demo` 用假数据起本地网页（口令 `demo`，DeepSeek/Notion/Telegram 都是假的），给前端调试用：
`RADAR_DATA=/tmp/rd AI_COUNCIL_HOME=/tmp/rd python -m radar demo`

## 测试

```
cd news-radar && python3 -m unittest discover -s tests -t .
```
