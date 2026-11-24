#!/usr/bin/env bash
# 在 Mini PC 上安装 / 更新资讯雷达（不需要 sudo，不碰 Docker、不碰 AI Council 的进程和文件）。
#   bash ~/news-radar-app/news-radar/deploy/install.sh          安装或更新，默认试运行（不推送）
# 做的事：
#   1. 备份 ~/ai-council/data/radar 和 runtime/radar 到 ~/ai-council/backups/news-radar-<时间>/
#   2. 建独立 Python 环境 ~/.local/share/news-radar/venv（httpx + 可选 pywebpush），不动 ~/ai-council/.venv
#   3. 跑离线测试，通过才继续
#   4. 装 systemd 用户服务 news-radar.service、news-radar-web.service 并启动
# 不删除任何文件。
set -euo pipefail
APP="$HOME/news-radar-app/news-radar"
HOME_AC="$HOME/ai-council"
VENV="$HOME/.local/share/news-radar/venv"
UNIT_DIR="$HOME/.config/systemd/user"
STAMP=$(date +%Y%m%d-%H%M%S)

[ -d "$APP/radar" ] || { echo "找不到 $APP，先把 feature/news-radar 分支克隆到 ~/news-radar-app"; exit 1; }
[ -d "$HOME_AC" ] || { echo "找不到 $HOME_AC"; exit 1; }

echo "== 1. 备份"
BK="$HOME_AC/backups/news-radar-$STAMP"
mkdir -p "$BK"
chmod 700 "$HOME_AC/backups" "$BK" 2>/dev/null || true
for d in data/radar runtime/radar; do
  [ -e "$HOME_AC/$d" ] && cp -a "$HOME_AC/$d" "$BK/$(echo $d | tr / _)"
done
for u in news-radar.service news-radar-web.service; do
  [ -e "$UNIT_DIR/$u" ] && cp -a "$UNIT_DIR/$u" "$BK/"
done
echo "   备份在 $BK"

echo "== 2. Python 环境"
if [ ! -x "$VENV/bin/python" ]; then
  mkdir -p "$(dirname "$VENV")"
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements.txt"
"$VENV/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements-webpush.txt" \
  || echo "   pywebpush 没装上：网页推送不可用，其他功能正常"
"$VENV/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements-images.txt" \
  || echo "   Pillow 没装上：配图存原图，不缩小"

echo "== 3. 离线测试"
(cd "$APP" && "$VENV/bin/python" -m unittest discover -s tests -t . 2>&1 | tail -3)

echo "== 4. 配置"
mkdir -p "$HOME_AC/runtime/radar" "$HOME_AC/data/radar" "$HOME_AC/logs/radar"
chmod 700 "$HOME_AC/runtime/radar" "$HOME_AC/data/radar"
ENVF="$HOME_AC/runtime/radar/.env"
if [ ! -e "$ENVF" ]; then
  (umask 077; printf 'RADAR_DRY_RUN=1\n' > "$ENVF")
  echo "   新建 $ENVF（试运行：只记日志不推送）"
fi
chmod 600 "$ENVF"

echo "== 5. systemd 用户服务"
mkdir -p "$UNIT_DIR"
cp "$APP/deploy/news-radar.service" "$APP/deploy/news-radar-web.service" "$UNIT_DIR/"
systemctl --user daemon-reload
systemctl --user enable news-radar.service news-radar-web.service >/dev/null
systemctl --user restart news-radar.service news-radar-web.service
sleep 3
systemctl --user --no-pager --lines=0 status news-radar.service news-radar-web.service | grep -E 'Loaded|Active' || true
echo
echo "完成。试运行中（不推送）。查看日志：tail -f $HOME_AC/logs/radar/app.log"
echo "手机网页：先运行一次 tailscale serve --bg --https=8443 http://127.0.0.1:8790"
echo "         然后 cd $APP && $VENV/bin/python -m radar web-url 显示登录链接"
