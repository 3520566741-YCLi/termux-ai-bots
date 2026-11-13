"""看门狗心跳 + 手机备份让位（standby）。

心跳：每 HEARTBEAT 秒
- 通知 systemd（sd_notify WATCHDOG=1）：事件循环卡死超过 WatchdogSec，systemd 会强制重启这个机器人；
- 写 DATA_DIR/heartbeat-<角色>（时间戳），独立的监控程序（scripts/monitor.py）据此判断机器人是否活着。

让位：同一个 Bot Token 另一台设备（手机备份）也在收消息时，Telegram 会报 Conflict。
在 CONFLICT_WINDOW 秒内收到 CONFLICT_LIMIT 次，就写 DATA_DIR/standby.json、在群里通知主人、退出进程，
退出码 STANDBY_EXIT（systemd 单元里 RestartPreventExitStatus=75，不会自动重启）。
另外两个机器人看到 standby.json 也会一起退出。主人回家停掉手机后执行 `council resume`（删掉文件并重启服务）。
"""
import asyncio
import json
import logging
import os
import socket
import time

from config import DATA_DIR

log = logging.getLogger('ai_council')

HEARTBEAT = 20
STANDBY_EXIT = 75
CONFLICT_LIMIT = 3
CONFLICT_WINDOW = 180
STANDBY_FILE = DATA_DIR / 'standby.json'
_conflicts: list = []


def sd_notify(message: str) -> None:
    """不依赖 systemd 库，直接往 NOTIFY_SOCKET 发数据报；不在 systemd 下运行时什么都不做。"""
    address = os.environ.get('NOTIFY_SOCKET')
    if not address:
        return
    if address.startswith('@'):
        address = '\0' + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode())
    except OSError:
        pass


def standby_active() -> bool:
    return STANDBY_FILE.exists()


def write_heartbeat(role: str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / f'heartbeat-{role}'
        tmp = path.with_suffix('.tmp')
        tmp.write_text(str(int(time.time())))
        tmp.replace(path)
    except OSError:
        pass


async def heartbeat(app, role: str) -> None:
    while True:
        write_heartbeat(role)
        sd_notify('WATCHDOG=1')
        if standby_active():
            log.warning('standby_detected role=%s action=stop', role)
            app.stop_running()
            return
        await asyncio.sleep(HEARTBEAT)


def install(app, role: str) -> None:
    original = app.post_init

    async def post_init(application):
        if original:
            await original(application)
        application.create_task(heartbeat(application, role))
        sd_notify('READY=1')
        log.info('watchdog_ready role=%s', role)

    app.post_init = post_init


async def on_conflict(app) -> None:
    now = time.monotonic()
    _conflicts.append(now)
    while _conflicts and now - _conflicts[0] > CONFLICT_WINDOW:
        _conflicts.pop(0)
    if len(_conflicts) < CONFLICT_LIMIT or standby_active():
        return
    role = app.bot_data['config'].role
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STANDBY_FILE.write_text(json.dumps({'since': int(time.time()), 'detected_by': role}))
    except OSError:
        pass
    log.warning('standby_enter role=%s reason=telegram_conflict', role)
    chats = sorted(c for c in app.bot_data['config'].chats if c < 0)[:1] or sorted(app.bot_data['config'].chats)[:1]
    for chat_id in chats:
        try:
            await app.bot.send_message(chat_id, '⚠️ 检测到另一台设备（应该是手机备份）正在运行机器人，Mini PC 已自动暂停全部三个机器人，'
                                                '避免抢消息。\n回家后：先在手机上停止机器人，再在 Mini PC 上执行 council resume。',
                                       parse_mode=None)
        except Exception:
            pass
    app.stop_running()
