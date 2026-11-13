#!/data/data/com.termux/files/usr/bin/bash
# 把官方 claude-desktop arm64 .deb 装进 proot-distro 的 Ubuntu（只装必需依赖），并加桌面图标。
# Install the official claude-desktop arm64 .deb into a proot-distro Ubuntu and add a desktop icon.
#
# 用法 / usage:
#   bash install-claude.sh /path/to/claude-desktop_<version>_arm64.deb <expected-sha256>
# 环境变量 DISTRO 指定 proot-distro 的发行版名（默认 ubuntu）。
set -eu
deb=${1:?'用法：install-claude.sh <deb> <sha256>'}
want=${2:?'需要官方公布的 SHA-256 / expected SHA-256 required'}
distro=${DISTRO:-ubuntu}
here=$(cd "$(dirname "$0")" && pwd)
rootfs="$PREFIX/var/lib/proot-distro/containers/$distro/rootfs"
test -d "$rootfs/usr" || rootfs="$PREFIX/var/lib/proot-distro/installed-rootfs/$distro"
test -d "$rootfs/usr" || { echo "ABORT 找不到 proot-distro 的 $distro（先 proot-distro install $distro）"; exit 1; }
test "$(sha256sum "$deb" | cut -d' ' -f1)" = "$want" || { echo 'ABORT 安装包 SHA-256 不对'; exit 1; }
cp "$deb" "$rootfs/root/claude-desktop.deb"
cp "$here/com.anthropic.Claude.desktop" "$here/claude-desktop-session" "$rootfs/root/"
echo "== apt"
proot-distro login "$distro" -- bash -c 'export DEBIAN_FRONTEND=noninteractive; apt-get update -q 2>&1 | tail -n 3; apt-get install -y -q --no-install-recommends /root/claude-desktop.deb gnome-keyring libsecret-1-0 libsecret-tools wmctrl dbus 2>&1 | tail -n 15; rm -f /root/claude-desktop.deb; dpkg -s claude-desktop | grep -E "^(Status|Version)"'
echo "== user"
# 以 root 运行时渲染进程会被 Electron 拒绝（白屏），所以用普通用户 claude 运行
proot-distro login "$distro" -- bash -c 'id claude >/dev/null 2>&1 || useradd -m -s /bin/bash claude
install -m 755 /root/claude-desktop-session /usr/local/bin/claude-desktop-session
install -d -o claude -g claude /home/claude/.local/share/applications
install -m 644 -o claude -g claude /root/com.anthropic.Claude.desktop /home/claude/.local/share/applications/
rm -f /root/claude-desktop-session /root/com.anthropic.Claude.desktop'
echo "== launcher"
mkdir -p "$HOME/.local/bin" "$HOME/Desktop"
sed "s/@DISTRO@/$distro/g" "$here/start-claude-desktop" > "$HOME/.local/bin/start-claude-desktop"
sed "s/@DISTRO@/$distro/g" "$here/maximize-claude-once" > "$HOME/.local/bin/maximize-claude-once"
chmod 700 "$HOME/.local/bin/start-claude-desktop" "$HOME/.local/bin/maximize-claude-once"
icon=$(cd "$rootfs" && ls usr/share/icons/hicolor/512x512/apps/*claude*.png usr/share/icons/hicolor/256x256/apps/*claude*.png usr/share/pixmaps/*claude*.png 2>/dev/null | head -n 1 || true)
[ -n "$icon" ] && icon="$rootfs/$icon" || icon=applications-internet
cat > "$HOME/Desktop/Claude.desktop" <<DESK
[Desktop Entry]
Version=1.0
Type=Application
Name=Claude
Comment=Claude desktop (Linux beta, ARM64, PRoot)
Exec=$HOME/.local/bin/start-claude-desktop
Icon=$icon
Terminal=false
Categories=Network;
DESK
chmod 711 "$HOME/Desktop/Claude.desktop"
echo INSTALL_DONE
