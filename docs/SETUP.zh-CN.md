# 安装指南

[English](SETUP.md) | **简体中文**

给人或 AI 助手照着做的分步说明。每一步最后都有 **检查** —— 没通过就不要进行下一步。除特别说明外，所有命令都在**手机的 Termux 里**运行。

密钥（机器人 token、API key）的规矩：

- 每个密钥都由本人在手机上输入到 `scripts/set_secret.py`（输入不显示）。它写进权限 600 的 `.env`，不会打印出来。
- 不要把密钥粘贴到 Telegram、AI 对话框、命令行参数里，也不要写进会提交到 Git 的文件。
- 万一泄露，到对应平台作废并重新生成。

下文的占位符：`example_deepseek_bot`、`example_claude_bot`、`example_codex_bot`（你的机器人用户名），`-1001234567890`（你的群 chat ID）。

---

## 1. 安装安卓应用

从 **F-Droid**（<https://f-droid.org>）或各应用的 **GitHub 发布页**安装 —— 不要用 Google Play 版（版本旧，且不能和插件互通）。所有 Termux 应用必须来自同一来源（签名要一致）。

- **Termux** —— 必需。
- **Termux:API** —— 必需（唤醒锁、定时任务、通知）。
- **Termux:Widget** —— 可选，主屏幕上的控制台按钮。
- **Termux:X11** —— 可选，只有 `extras/` 里的桌面功能需要。

打开一次 Termux，等它完成首次初始化。

**检查：** Termux 能打开并出现命令提示符。（第 3 步后 `termux-battery-status` 输出 JSON，说明 Termux:API 可用。）

## 2. 别让手机杀掉 Termux

安卓（尤其是华为 / HarmonyOS 手机）会积极清理后台。

- 设置 → 电池 → 应用启动管理 → Termux：改为**手动管理**，允许自启动、关联启动、后台活动。Termux:API 同样设置。
- 关闭 Termux 的电池优化；手机保持充电。
- 下拉 Termux 通知，点 **Acquire wakelock**（机器人启动时也会调用 `termux-wake-lock`）。
- 如果要用代理/VPN 应用（例如 Clash）访问 Telegram/OpenAI/Anthropic，它也要同样设置并保持常开。

**检查：** 锁屏 10 分钟后解锁，Termux 的通知还在。

## 3. 安装 Termux 软件包

```bash
pkg update
pkg install -y python git proot termux-api openssh rsync
```

Codex 需要 `proot`（见第 9 步）。`openssh` 只有 `/code` 和存文件才需要。

**检查：** `python --version && git --version && proot --version | head -n 1` 输出三个版本号，`termux-battery-status` 输出 JSON。

## 4. 获取代码，建 Python 环境

```bash
git clone https://github.com/3520566741-YCLi/termux-ai-bots.git ~/ai-bots-src
cp -r ~/ai-bots-src/ai-council ~/ai-council
chmod 700 ~/ai-council
cd ~/ai-council
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

**检查：** `cd ~/ai-council && .venv/bin/python -m unittest discover -s tests` 最后显示 `OK`（离线测试，不需要密钥）。

## 5. 建三个 Telegram 机器人和一个群

1. 在 Telegram 打开 **@BotFather**，发三次 `/newbot` —— 每个 AI 一个机器人，例如 `example_deepseek_bot`、`example_claude_bot`、`example_codex_bot`。BotFather 会显示每个机器人的 token；先别关这个聊天，第 7 步要在手机上输入。
2. 建一个群，把三个机器人都拉进来。
3. 把三个机器人都设为群**管理员**。默认“隐私模式”下，机器人在群里只收到命令和 @/回复它的消息；设为管理员后能收到所有消息，普通消息才能交给 DeepSeek。代码保证只有一个机器人回答。（也可以在 BotFather 里 `/setprivacy` → Disable，然后把机器人移出群再加回。）
4. 如果要改群设置（例如新成员可见历史），现在就改：群升级为 **supergroup 会改变 chat ID**。

**检查：** 群成员列表里三个机器人都带管理员标记。

## 6. 建 Codex 和 Claude 的实例目录

`~/ai-council` 放真正的代码（角色 `deepseek`）。另外两个实例有自己的 `.env`、`logs/`、`memory_store/`，其余都用符号链接指回 `~/ai-council`：

```bash
ac="$HOME/ai-council"

cx="$HOME/ai-codex"
mkdir -p "$cx/logs" "$cx/memory_store" "$ac/tools/codex-cloud/bin"; chmod 700 "$cx"
for f in agents gateway orchestrator config.py start.sh stop.sh scripts tests tools .venv requirements.txt; do ln -s "$ac/$f" "$cx/$f"; done

cl="$HOME/ai-claude"
mkdir -p "$cl/logs" "$cl/memory_store"; chmod 700 "$cl"
for f in agents gateway orchestrator config.py start.sh stop.sh scripts tests .venv requirements.txt; do ln -s "$ac/$f" "$cl/$f"; done
```

（`tools/` 只链接到 Codex 实例，里面放 `codex` 程序。）

**检查：** `ls -l ~/ai-codex ~/ai-claude` 显示的符号链接都指向 `/data/data/com.termux/files/home/ai-council/`。

## 7. 输入机器人 token、API key 和 chat ID

`set_secret.py` 用不显示的方式读取输入，检查格式，写进指定实例的 `.env`（600），再做一次免费的验证调用。每个实例都用同一个 Python：

```bash
S="$HOME/ai-council/.venv/bin/python $HOME/ai-council/scripts/set_secret.py"
```

**7a. DeepSeek 实例**（`~/ai-council`）。先在 <https://platform.deepseek.com> 创建 API key。

```bash
$S --instance ~/ai-council --field TELEGRAM_BOT_TOKEN --role deepseek   # 输入 example_deepseek_bot 的 token
$S --instance ~/ai-council --field DEEPSEEK_API_KEY
```

**7b. 找 chat ID。** 在群里发 `/start@example_deepseek_bot`，也可以私聊机器人发一句话。然后（机器人**没在运行**时）：

```bash
~/ai-council/.venv/bin/python ~/ai-council/scripts/find_chat_id.py --instance ~/ai-council
```

它会输出 `chat-id  类型  名称`。群 ID 是负数（supergroup 以 `-100` 开头）；你的私聊 ID 是正数。允许这个群，愿意的话加上私聊。**负数要用 `=` 连接：**

```bash
$S --instance ~/ai-council --chats=-1001234567890,123456789
```

**7c. Claude 实例**（`~/ai-claude`）。在 <https://console.anthropic.com> 创建 key。

```bash
$S --instance ~/ai-claude --field TELEGRAM_BOT_TOKEN --role claude --copy-chats ~/ai-council/.env
$S --instance ~/ai-claude --field ANTHROPIC_API_KEY
```

可选：在 `~/ai-claude/.env` 写 `CLAUDE_MODEL=...`（默认 `claude-sonnet-5-5`）。

**7d. Codex 实例**（`~/ai-codex`）—— 不需要 API key，用你的 ChatGPT 登录（第 9 步）。

```bash
$S --instance ~/ai-codex --field TELEGRAM_BOT_TOKEN --role codex --copy-chats ~/ai-council/.env
```

各 `.env` 的内容（不显示值）：

| 实例 | 字段 |
|---|---|
| `~/ai-council` | `BOT_ROLE=deepseek`、`TELEGRAM_BOT_TOKEN`、`ALLOWED_CHAT_IDS`、`DEEPSEEK_API_KEY`，可选 `DEEPSEEK_MODEL` / `DEEPSEEK_BASE_URL` |
| `~/ai-claude` | `BOT_ROLE=claude`、`TELEGRAM_BOT_TOKEN`、`ALLOWED_CHAT_IDS`、`ANTHROPIC_API_KEY`，可选 `CLAUDE_MODEL` |
| `~/ai-codex` | `BOT_ROLE=codex`、`TELEGRAM_BOT_TOKEN`、`ALLOWED_CHAT_IDS` |

**检查**（只输出角色和 chat ID，不输出密钥）：

```bash
for d in ai-council ai-claude ai-codex; do (cd ~/$d && AI_BOT_ROOT=$PWD .venv/bin/python -c 'from config import load_config; c = load_config(); print(c.role, sorted(c.chats))'); done
stat -c '%a %n' ~/ai-council/.env ~/ai-claude/.env ~/ai-codex/.env    # 都应是 600
```

## 8. 安装 ai-hub 控制台

```bash
src=~/ai-bots-src/ai-hub
install -Dm700 $src/ai_hub.py ~/.local/bin/ai-hub
mkdir -p ~/.local/share/ai-hub ~/.config/ai-hub && chmod 700 ~/.config/ai-hub
cp -r $src/web ~/.local/share/ai-hub/
install -m 600 $src/ADDING_AI.md ~/.local/share/ai-hub/
install -m 700 $src/ensure.sh ~/.local/share/ai-hub/ensure.sh
[ -f ~/.config/ai-hub/bots.json ] || install -m 600 $src/bots.example.json ~/.config/ai-hub/bots.json
mkdir -p ~/.shortcuts && install -m 700 $src/AI-Bots ~/.shortcuts/AI-Bots      # Termux:Widget 按钮
```

确认 `~/.local/bin` 在 `PATH` 里（在 `~/.bashrc` 加 `export PATH="$HOME/.local/bin:$PATH"`，再开一个新会话）。

可选 —— 让安卓每 15 分钟拉起一次守护，即使 Termux 被杀过：

```bash
termux-job-scheduler --job-id 4242 --period-ms 900000 --script ~/.local/share/ai-hub/ensure.sh
```

**检查：** `ai-hub status` 列出 `deepseek`、`codex`、`claude`，都是“已停止”。长按主屏幕 → 小组件 → Termux:Widget → 添加，点 **AI-Bots**：浏览器打开控制台。

## 9. 安装并登录 Codex 命令行

1. 打开 <https://github.com/openai/codex/releases/latest>，找 **aarch64 Linux musl** 版本的 `codex` 和 `codex-code-mode-host`（文件名含 `aarch64-unknown-linux-musl`；`/code` 需要 `codex-code-mode-host`）。下载到 `~/ai-council/tools/codex-cloud/bin/`：

   ```bash
   cd ~/ai-council/tools/codex-cloud/bin
   curl -LO https://github.com/openai/codex/releases/download/<tag>/<codex 文件名>.tar.gz
   curl -LO https://github.com/openai/codex/releases/download/<tag>/<code-mode-host 文件名>.tar.gz
   ```

2. 用发布页核对 SHA-256（GitHub 在每个文件旁显示 `sha256:` 摘要，接口里也有）：

   ```bash
   sha256sum *.tar.gz
   curl -s https://api.github.com/repos/openai/codex/releases/latest | ~/ai-council/.venv/bin/python -c 'import json,sys; [print(a.get("digest"), a["name"]) for a in json.load(sys.stdin)["assets"] if "aarch64-unknown-linux-musl" in a["name"]]'
   ```

   两边必须完全一致。不一致就删掉文件，停止。

3. 解压，并把程序准确命名为 `codex` 和 `codex-code-mode-host`：

   ```bash
   for f in *.tar.gz; do tar -xzf "$f"; done
   ls    # 改名，例如  mv codex-aarch64-unknown-linux-musl codex
   chmod 755 codex codex-code-mode-host
   rm -f *.tar.gz
   ```

4. 用 ChatGPT 账号登录。musl 程序要读 `/etc/resolv.conf`，安卓没有这个文件，所以只有在 `proot` 里把 Termux 的 resolv.conf 挂上去 DNS 才能用；还要指定 Termux 的 CA 证书：

   ```bash
   cd ~/ai-council/tools/codex-cloud/bin
   CODEX_CA_CERTIFICATE=$PREFIX/etc/tls/cert.pem proot -b $PREFIX/etc/resolv.conf:/etc/resolv.conf ./codex -c 'cli_auth_credentials_store="file"' login
   ```

   在手机浏览器打开显示的链接，用 ChatGPT 登录。如果浏览器回不到本机回调地址，把命令里的 `login` 换成 `login --device-auth`，按提示输入验证码。

其余的代码已经处理好：机器人在 `proot` 里运行 `codex --search ... exec`（同样挂 resolv.conf），对 Codex 屏蔽 `.env`、`~/.ssh`、`~/.config/ai-hub`、`~/.config/ai-tools`，并给只读的 GitHub MCP 服务设置 `default_tools_approval_mode="approve"`（否则 `exec` 模式下 MCP 调用会被自动拒绝）。

**检查：** 把同一命令里的 `login` 换成 `login status`，输出 `Logged in using ChatGPT`。

## 10. 共用工具（可选）：GitHub、Tavily、Notion

这四个值都写进 `~/.config/ai-tools/.env`（目录 700，文件 600）—— 这些字段 `set_secret.py` 会自动选这个文件。

- **GitHub** —— <https://github.com/settings/personal-access-tokens> → 细粒度令牌（fine-grained）→ Repository access 选 **All repositories** → 权限只给 **Contents: Read-only**（Metadata 只读会自动加上）。别的都不要。
- **Tavily**（DeepSeek 的联网搜索）—— 在 <https://app.tavily.com> 获取 key。
- **Notion**（只给 DeepSeek）—— <https://www.notion.so/profile/integrations> → 新建**内部（internal）**连接 → 能力选 **读取内容、更新内容、插入内容**（用户信息：不需要或不含邮箱）。在连接的 **内容访问权限（Content access）** 里勾选它能看到的页面。建一个保存笔记用的页面（例如“AI 笔记”）并勾选；页面 ID 是链接末尾的 32 位十六进制 —— 直接粘贴整个链接也行。

```bash
$S --field GITHUB_TOKEN
$S --field TAVILY_API_KEY
$S --field NOTION_TOKEN
$S --field NOTION_PARENT_PAGE_ID
```

**检查：** 每条命令都报告验证结果（GitHub 显示你的账号名；Notion 确认页面能访问）。

## 11. 启动机器人

```bash
ai-hub start deepseek
ai-hub start claude
ai-hub start codex
ai-hub status
```

（不用 ai-hub 也行：`bash ~/ai-council/start.sh`、`bash ~/ai-claude/start.sh`、`bash ~/ai-codex/start.sh`。）

**检查：** 每个日志里都有 `bot_ready`：

```bash
for d in ai-council ai-claude ai-codex; do grep bot_ready ~/$d/logs/bot.log | tail -n 1; done
```

其中有角色、模型、已配置的 `tools=` 和 `sees_all_group_messages`（只有关掉隐私模式才是 `True`；是管理员的话即使显示 `False` 也能收到所有消息）。

## 12. 绑定 Codex 主人

Codex 用你的订阅，而且能读手机上的文件，所以只允许一个 Telegram 用户（你）使用。发起讨论也要求先绑定。

```bash
bash ~/ai-codex/scripts/codex_bind_window.sh
```

（或在控制台 Codex 卡片上点 **开放 Codex 绑定（30 分钟）**。）30 分钟内在群里发 `/codexbind@example_codex_bot`。

**检查：** `~/ai-codex/codex_owner.json` 已存在，机器人回复“已绑定”。以后要换人，删掉这个文件再做一遍。

## 13. 在 Telegram 里测试

1. 群里发：`你好，用一句话介绍你自己` → 只有 DeepSeek 回答。
2. `@example_claude_bot 你好` → 只有 Claude 回答。`@example_codex_bot 你好` → 只有 Codex 回答（几十秒）。
3. 发一张带说明的图片 → DeepSeek 描述图片。
4. `讨论 1轮 用一句话说说早睡的好处` → 三段短发言 + Codex 总结。

**检查：** `grep reply_sent ~/ai-council/logs/bot.log | tail -n 3` 显示 token 数，控制台显示今日费用。

## 14. 可选：`/code` 和把文件存到 GitHub（Codex）

`/code 任务` 让 Codex 在手机上的仓库副本里改代码、提交，并推一个新的 `codex/<时间>` 分支（绝不推 `main`）。发给 Codex 机器人的文件存到同一仓库的 `files` 分支。

1. 给手机建 SSH 密钥，在那个仓库里加为**有写权限的部署密钥（deploy key）**：

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N '' -C 'termux-codex'
   cat ~/.ssh/id_ed25519.pub     # 粘贴到 GitHub → 仓库 → Settings → Deploy keys（勾选允许写入）
   ssh -T git@github.com         # 如果 22 端口被封，在 ~/.ssh/config 里改用 ssh.github.com 的 443 端口
   ```

2. 创建 `~/ai-codex/codex_code.json`（权限 600）：

   ```json
   {"repo": "git@github.com:YOUR_USER/YOUR_REPO.git", "web": "https://github.com/YOUR_USER/YOUR_REPO", "dir": "~/codex-work/YOUR_REPO"}
   ```

3. 要存文件，先建一次空的 `files` 分支（例如 `git switch --orphan files && git commit --allow-empty -m files && git push origin files`）。

安卓不 root 没有用户命名空间，所以 `/code` 只能用 `danger-full-access`；机器人用 proot 屏蔽密钥、推送前扫描改动里疑似密钥的内容、并报告仓库以外被改动的文件。这是约束和事后检查，不是隔离。

**检查：** `/code 在 README 加一行 hello` → 回复里有 `compare/main...codex/...` 链接。

---

## 安全说明

- 允许的聊天里**每个人**都能用 DeepSeek 和 Claude —— 包括它们的工具。配置了 GitHub 令牌后，**他们能通过机器人读你的私人仓库**；通过 DeepSeek 还能读取和追加你共享给连接的 Notion 页面。只允许你完全信任的聊天。
- Codex 只给主人用，因为它能在手机上执行命令，而且它自己的登录文件无法对它屏蔽。
- 机器人日志只记事件名和数字（没有消息正文、没有密钥）。ai-hub 显示时还会隐藏 chat/用户 ID 和疑似密钥。
- 控制台只监听 127.0.0.1，需要随机 token；20 分钟没人访问自动退出。
- 机器人重启时会故意丢弃排队中的 Telegram 消息（避免重复付费）。

## 更新

```bash
cd ~/ai-bots-src && git pull
rsync -a --exclude .env --exclude .venv --exclude logs --exclude memory_store --exclude tools \
      --exclude codex_owner.json --exclude codex_code.json ~/ai-bots-src/ai-council/ ~/ai-council/
cd ~/ai-council && .venv/bin/python -m unittest discover -s tests && for b in deepseek codex claude; do ai-hub restart $b; done
install -m 700 ~/ai-bots-src/ai-hub/ai_hub.py ~/.local/bin/ai-hub && cp -r ~/ai-bots-src/ai-hub/web ~/.local/share/ai-hub/
```

## 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| 机器人过一阵就停了，`bot.log` 最后是网络错误、没有 `bot_stopped` | 安卓杀了 Termux。重做第 2 步（华为：应用启动管理 → 手动管理）。保留第 8 步的守护定时任务。Android 12 以上仍然发生的话，查 Termux wiki 里的 “phantom process killer”。 |
| 一直没有 `bot_ready`；`launcher.log` 显示 `NetworkError` | 连不上 Telegram。检查 Wi-Fi 和代理/VPN（例如 Clash）。机器人会一直重试，网络恢复后自动连上。华为不允许读 `/sys/class/net`，ai-hub 判断不了 VPN 是否开启。 |
| 群里只有命令和 @ 有反应，普通消息没人理 | 隐私模式。把机器人设为管理员（第 5 步），或在 BotFather 关闭隐私模式后重新拉进群。 |
| 同一条消息两个机器人都回答 | 两个实例的 `BOT_ROLE` 一样（只有 `deepseek` 回答没点名的消息）。做第 7 步的检查。 |
| 以前能用的群，机器人不理了 | 群升级成了 supergroup，ID 变了。重新运行 `find_chat_id.py`，用 `--chats=` 更新。 |
| `set_secret.py` 提示“格式不对” | 复制时多带了文字或少复制了一段。终端粘贴保护字符（`ESC[200~`/`ESC[201~`）已经会自动去掉。到平台重新复制。 |
| `--chats -100…` 报 “expected one argument” | 写成 `--chats=-100…`（负数会被当成选项）。 |
| Codex“暂时不可用” / 电脑上能登录、手机上不行 | DNS：Codex 必须在 `proot -b $PREFIX/etc/resolv.conf:/etc/resolv.conf` 里运行（第 9 步）。再检查 `login status`，以及是否装了 `proot`。 |
| Codex 用不了 GitHub 工具 | MCP 服务需要 `default_tools_approval_mode="approve"`（代码已设）和 `~/.config/ai-tools/.env` 里的 `GITHUB_TOKEN`；加了之后重启 Codex 机器人。 |
| `/code` 提示 “codex_code.json” | 按第 14 步创建。克隆/推送失败说明没有部署密钥或没给写权限。 |
| 讨论提示 Codex/Claude 没配置好 | 它们的 `.env` 要能加载（第 7 步检查），机器人要在群里。只有绑定的 Codex 主人能发起讨论。 |
| Claude 回复“不便回答” | 自动换模型重试后仍被 Anthropic 安全系统拒绝。换个问法。 |
| 想在电脑上输入密钥（adb） | 见下面的附录。 |

## 附录：用电脑经 adb 发送密钥（可选）

想在电脑上打字的话，`scripts/receive_bot_token.py` 在手机的 `127.0.0.1:38421` 上一次性监听（10 分钟超时），电脑用 USB 转发端口。

手机（Termux），在实例目录里：

```bash
cd ~/ai-claude && AI_BOT_ROOT=$PWD .venv/bin/python scripts/receive_bot_token.py --role claude --field ANTHROPIC_API_KEY
```

电脑（打开 USB 调试）：

```bash
adb forward tcp:38421 tcp:38421
# zsh（macOS 默认）—— 注意是  变量名?提示  的写法；zsh 里 `read -p` 是另一个意思：
read -rs 'k?Secret: '; echo; printf '%s\n' "$k" | nc 127.0.0.1 38421; unset k
# bash：
read -rsp 'Secret: ' k; echo; printf '%s\n' "$k" | nc 127.0.0.1 38421; unset k
adb forward --remove tcp:38421
```

macOS 终端粘贴时会加上“粘贴保护”控制符；接收脚本会去掉它们并提取密钥。共用工具的密钥放 `~/.config/ai-tools`：运行接收脚本时用 `AI_BOT_ROOT=~/.config/ai-tools`、`--role deepseek`（必填但不用）和 `--field GITHUB_TOKEN`（或 `TAVILY_API_KEY`、`NOTION_TOKEN`）。
