"""Vesper 的长期记忆：Mini PC 当“记忆中心”，手机助手和 Mac 上 Deck 里的 Vesper 共用一份。

- 每条记忆有全局 uid、更新时间 updated、删除标记 deleted（删除也要同步，所以留“墓碑”）；
- 冲突规则：谁的 updated 新听谁的（两台机器都用网络对时）；
- Mac 端是独立的同步小程序（mac-butler 仓库 manager/memsync.py），不改 Vesper 本身的代码；
- 同步用“设备配对”：Mac 端自己生成随机令牌，只把它的哈希发来申请，用户在手机网页上点“允许”后才生效。
  令牌明文只存在 Mac 上，这里只存哈希。
"""
import hashlib
import hmac
import re
import secrets
import time
import uuid


MAX_TEXT = 300
MEMORY_IN_PROMPT = 120
UID_RE = re.compile(r'^[0-9a-f]{32}$')


def _row(r) -> dict:
    return {'uid': r['uid'], 'text': r['text'], 'kind': r['kind'], 'created': r['created'], 'updated': r['updated'],
            'deleted': r['deleted'], 'origin': r['origin']}


def active(db, limit: int = 500) -> list:
    return [_row(r) for r in db.execute('SELECT * FROM memories WHERE deleted=0 ORDER BY updated DESC LIMIT ?', (limit,))]


def save(db, text: str, kind: str = '', origin: str = 'phone') -> dict:
    text = (text or '').strip()[:MAX_TEXT]
    if not text:
        raise ValueError('记忆是空的')
    dup = db.execute('SELECT * FROM memories WHERE text=? AND deleted=0', (text,)).fetchone()
    if dup:
        return _row(dup)
    now = time.time()
    uid = uuid.uuid4().hex
    db.execute('INSERT INTO memories(uid, text, kind, created, updated, deleted, origin) VALUES(?,?,?,?,?,0,?)',
               (uid, text, (kind or '')[:20], now, now, origin))
    return {'uid': uid, 'text': text, 'kind': kind, 'created': now, 'updated': now, 'deleted': 0, 'origin': origin}


def find(db, ref: str):
    """按 uid 或 uid 前缀（至少 6 位）找一条没删的记忆。"""
    ref = (ref or '').strip().lower()
    if len(ref) < 6 or not re.fullmatch(r'[0-9a-f]+', ref):
        return None
    rows = db.execute('SELECT * FROM memories WHERE uid LIKE ? AND deleted=0', (ref + '%',)).fetchall()
    return rows[0] if len(rows) == 1 else None


def update(db, ref: str, text: str) -> dict:
    row = find(db, ref)
    if not row:
        raise ValueError('找不到这条记忆')
    text = (text or '').strip()[:MAX_TEXT]
    if not text:
        raise ValueError('记忆是空的')
    db.execute('UPDATE memories SET text=?, updated=? WHERE uid=?', (text, time.time(), row['uid']))
    return {'uid': row['uid'], 'old': row['text'], 'text': text}


def delete(db, ref: str) -> dict:
    row = find(db, ref)
    if not row:
        raise ValueError('找不到这条记忆')
    db.execute('UPDATE memories SET deleted=1, updated=? WHERE uid=?', (time.time(), row['uid']))
    return {'uid': row['uid'], 'text': row['text']}


def prompt_text(db) -> str:
    mems = active(db, MEMORY_IN_PROMPT)
    return '\n'.join(f'[{m["uid"][:8]}]{"（" + m["kind"] + "）" if m["kind"] else ""} {m["text"]}' for m in mems) or '（还没有）'


# ---------- 同步 ----------
def changes(db, since: float, limit: int = 1000) -> list:
    return [_row(r) for r in db.execute('SELECT * FROM memories WHERE updated > ? ORDER BY updated LIMIT ?',
                                        (since, limit))]


def apply(db, items: list, origin: str) -> int:
    """Mac 推来的改动：新的就插入，比本地新的就覆盖（包括删除），旧的忽略。返回实际改了几条。"""
    n = 0
    now = time.time()
    for it in items[:2000]:
        if not isinstance(it, dict):
            continue
        uid = str(it.get('uid') or '').lower()
        if not UID_RE.match(uid):
            continue
        try:
            updated = min(float(it.get('updated') or 0), now + 300)   # 不接受远在未来的时间
            created = float(it.get('created') or updated)
        except (TypeError, ValueError):
            continue
        text = str(it.get('text') or '').strip()[:MAX_TEXT]
        deleted = 1 if it.get('deleted') else 0
        if not text and not deleted:
            continue
        kind = str(it.get('kind') or '')[:20]
        cur = db.execute('SELECT updated, text FROM memories WHERE uid=?', (uid,)).fetchone()
        if cur is None:
            db.execute('INSERT INTO memories(uid, text, kind, created, updated, deleted, origin) VALUES(?,?,?,?,?,?,?)',
                       (uid, text or '(已删除)', kind, created, updated, deleted, origin))
            n += 1
        elif updated > cur['updated']:
            db.execute('UPDATE memories SET text=?, kind=?, updated=?, deleted=? WHERE uid=?',
                       (text or cur['text'], kind, updated, deleted, uid))
            n += 1
    return n


# ---------- 设备配对 ----------
def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def request_pair(db, name: str, thash: str) -> str:
    if not re.fullmatch(r'[0-9a-f]{64}', thash or ''):
        raise ValueError('令牌哈希不对')
    # 没人批准的配对请求 10 分钟后自动作废，别人刷请求也堵不住真正的 Mac
    db.execute('DELETE FROM devices WHERE approved=0 AND created < ?', (time.time() - 600,))
    pending = db.execute('SELECT COUNT(*) FROM devices WHERE approved=0').fetchone()[0]
    if pending >= 5:
        raise ValueError('待批准的设备太多了，先在手机上处理')
    dev = secrets.token_hex(4)
    db.execute('INSERT INTO devices(id, name, thash, approved, created, last_seen) VALUES(?,?,?,0,?,0)',
               (dev, re.sub(r'[\x00-\x1f]', '', name or '未命名设备')[:40], thash, time.time()))
    return dev


def device_for(db, token: str):
    if not token:
        return None
    h = token_hash(token)
    for r in db.execute('SELECT * FROM devices'):
        if hmac.compare_digest(r['thash'], h):
            if r['approved']:
                db.execute('UPDATE devices SET last_seen=? WHERE id=?', (time.time(), r['id']))
            return dict(r)
    return None


def devices(db) -> list:
    return [{'id': r['id'], 'name': r['name'], 'approved': bool(r['approved']), 'created': r['created'],
             'last_seen': r['last_seen']} for r in db.execute('SELECT * FROM devices ORDER BY created DESC')]
