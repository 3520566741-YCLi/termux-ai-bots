# Claude desktop app in PRoot (optional)

**English** | [简体中文](#简体中文)

Runs Anthropic's Linux desktop app (arm64 `.deb`, beta) inside a `proot-distro` Ubuntu, shown on the Termux:X11 desktop. Not needed for the bots.

Requirements: Termux:X11 with a running desktop on `DISPLAY=:1`, `pkg install proot-distro`, `proot-distro install ubuntu`, and the official `claude-desktop_<version>_arm64.deb` plus its published SHA-256 (download it yourself from Anthropic; it is not in this repo).

```bash
bash install-claude.sh ~/Downloads/claude-desktop_<version>_arm64.deb <sha256>   # DISTRO=ubuntu by default
~/.local/bin/start-claude-desktop      # or tap the Claude icon on the desktop
```

| File | Role |
|---|---|
| `install-claude.sh` | checks the SHA-256, installs the deb with only required deps (+ gnome-keyring, libsecret, wmctrl), creates the PRoot user `claude`, installs the launchers and a desktop icon |
| `start-claude-desktop` | Termux-side entry: runs `claude-desktop-session` as user `claude` in PRoot; logs to `~/.cache/claude-desktop.log` |
| `claude-desktop-session` | goes to `/usr/local/bin/` in PRoot: starts gnome-keyring with a password-less `login` keyring, then Claude with `--no-sandbox --disable-gpu --in-process-gpu --no-zygote --password-store=gnome-libsecret` |
| `maximize-claude-once` | maximizes the window once it appears (small screen) |
| `com.anthropic.Claude.desktop` | per-user `claude://` handler so the browser sign-in callback reaches the running app |

Why it is set up this way:

- **Not root**: as root, Electron's renderer refuses to start without `--no-sandbox` → white window. `ELECTRON_DISABLE_SANDBOX=1` does not help and Claude rejects `--browser-subprocess-path`, so a normal user is used.
- **Keyring**: without a secret service Claude's safeStorage is unavailable and sign-in is not kept. An empty-password `--unlock` pops a gcr dialog and fails unattended, so a pre-written password-less keyring is used. Credentials are therefore not encrypted, but stay in Termux's private storage.
- **Sign-in callback**: after signing in in the browser, "Open Claude" starts a second process via `claude://`; it also needs the phone-specific flags, otherwise it exits and the callback never reaches the open window.
- Do not start it wrapped in `timeout` from a Termux terminal: Claude reads the shell environment (`bash -l -i`), grabs the terminal and the process group gets suspended.
- Sound: set `PULSE_SERVER` (e.g. `tcp:127.0.0.1:4713`) before starting if you run a PulseAudio server in Termux.

## 简体中文

在 `proot-distro` 的 Ubuntu 里运行 Anthropic 官方 Linux 桌面版（arm64 `.deb`，beta），显示在 Termux:X11 桌面上。机器人用不到。

- 需要：Termux:X11 桌面（`DISPLAY=:1`）、`pkg install proot-distro`、`proot-distro install ubuntu`，以及官方 `claude-desktop_<版本>_arm64.deb` 和它公布的 SHA-256（自己从 Anthropic 下载，仓库里没有）。
- 安装：`bash install-claude.sh <deb 路径> <sha256>`；启动：`~/.local/bin/start-claude-desktop` 或点桌面图标。
- 为什么用普通用户：root 下 Electron 渲染进程没有 `--no-sandbox` 会退出（白屏），而 Claude 拒绝 `--browser-subprocess-path` 等参数。
- 钥匙串：没有 secret service 时登录保存不住；空密码解锁会弹确认框，无人值守会失败，所以预先写一个无密码钥匙串（凭据不加密，但只在 Termux 私有目录）。
- 登录回调：浏览器登录后点 “Open Claude”，第二个进程也要带手机必需的参数，否则回调到不了已打开的窗口，所以装了用户级 `claude://` 处理器。
- 不要在 Termux 终端里用 `timeout` 包着启动（会抢终端导致进程组被挂起）。
