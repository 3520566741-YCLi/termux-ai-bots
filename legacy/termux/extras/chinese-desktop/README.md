# Chinese UI for the Termux XFCE desktop (optional)

**English** | [简体中文](#简体中文)

Only relevant if you run the native Termux XFCE desktop in Termux:X11. Not needed for the bots.

## Why `LANG=zh_CN.UTF-8` is not enough

- Termux packages ship without `share/locale` translation files.
- Termux's GLib is not linked against libintl, so `g_dgettext` and friends just return the English string.
- With only `LANG` set, you get Chinese only where `.desktop` files carry `Name[zh_CN]` (menus, desktop icons) and in Chromium.

## The shim

`zh-gettext.c` is a tiny `LD_PRELOAD` library that overrides GLib's exported `g_dgettext`, `g_dcgettext`, `g_dngettext`, `g_dpgettext`, `g_dpgettext2` and looks strings up in `$ZH_GETTEXT_DIR` (default `~/.local/share/locale`)`/<lang>/LC_MESSAGES/<domain>.mo`. When no domain is given it uses the package passed to `xfce_textdomain()` (needed for menu bars), otherwise the program name. Not a Chinese locale, no file, or no entry → the English original, so nothing breaks.

## Getting the `.mo` files (not included in this repo)

Take the `zh_CN` translations from **Debian 13 (trixie)** packages of the same XFCE 4.20 / GTK 3 versions Termux ships (`pkg show xfce4-session` to check). Download the `.deb`s from <https://packages.debian.org/trixie/> (or a Debian mirror), verify them, then extract just the translations — on any Linux/macOS machine or in Termux (`pkg install binutils` for `ar`):

```bash
mkdir mo && for deb in *.deb; do
  d=$(mktemp -d); (cd "$d" && ar x "$OLDPWD/$deb" && tar -xf data.tar.*)
  cp "$d"/usr/share/locale/zh_CN/LC_MESSAGES/*.mo mo/ 2>/dev/null; rm -rf "$d"
done
```

Useful packages: `xfce4-panel`, `xfce4-session`, `xfce4-settings`, `xfdesktop4-data`, `xfwm4`, `thunar-data`, `libxfce4ui-common`, `libxfce4util-common`, `libgarcon-common`, `libgtk-3-common` (gives `gtk30.mo`, `gtk30-properties.mo`), `xfce4-terminal`, `xfce4-appfinder`, `xfce4-notifyd`, `xfce4-power-manager-data`, `xfce4-whiskermenu-plugin`, `mousepad`, `ristretto`, `tumbler-common`.

## Install

```bash
pkg install clang
L=~/.local/share/locale/zh_CN/LC_MESSAGES; mkdir -p $L ~/.local/lib
cp mo/*.mo $L/
clang -shared -fPIC -O2 -Wall -o ~/.local/lib/libzh-gettext.so zh-gettext.c
```

In the script that starts your desktop, **before** `xfce4-session` starts, add:

```bash
export LANG=zh_CN.UTF-8 LANGUAGE=zh_CN:zh
export LD_PRELOAD="${LD_PRELOAD:+$LD_PRELOAD:}$HOME/.local/lib/libzh-gettext.so"   # keep termux-exec
```

Undo: remove those two lines.

## Known limits

- Strings formatted with an inlined `ngettext()` at build time (e.g. Thunar's "14 folders" status bar) stay English.
- Weekday names come from bionic's `strftime` and are English only; leave them out of the clock format.
- Programs inside a proot-distro (e.g. the Claude desktop app) are not affected.

## 简体中文

只有在 Termux:X11 里跑 Termux 原生 XFCE 桌面才需要，机器人用不到。

- 原因：Termux 的包删掉了 `share/locale` 翻译文件，GLib 也没链接 libintl，`g_dgettext` 等直接返回英文；只设 `LANG` 只有 `.desktop` 里的中文名和 Chromium 会变中文。
- 做法：`zh-gettext.c` 是一个 `LD_PRELOAD` 小库，接管 GLib 的 `g_dgettext` 系列，到 `~/.local/share/locale/<语言>/LC_MESSAGES/<domain>.mo` 查中文；找不到就原样返回英文。
- `.mo` 文件不放在仓库里：从 **Debian 13 (trixie)** 同版本（XFCE 4.20、GTK 3）的包里提取 `usr/share/locale/zh_CN/LC_MESSAGES/*.mo`，命令和包名见上文。
- 安装：`clang` 编译到 `~/.local/lib/libzh-gettext.so`，在桌面启动脚本里 `xfce4-session` 之前设置 `LANG=zh_CN.UTF-8 LANGUAGE=zh_CN:zh`，并把库追加到 `LD_PRELOAD`（保留原有的 termux-exec）。删掉这两行即可恢复英文。
- 限制：Thunar 状态栏计数这类编译时内联的文字仍是英文；星期只有英文；PRoot 里的程序不受影响。
