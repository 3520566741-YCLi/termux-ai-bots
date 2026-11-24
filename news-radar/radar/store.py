"""SQLite 存储：资讯、关注列表、设置、网页推送订阅、聊天记录、用量。

每次操作单独开连接（WAL 模式），抓取协程和网页线程可以同时读写。
"""
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

from . import config

SCHEMA = '''
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  uid TEXT NOT NULL,
  fp TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  market TEXT NOT NULL DEFAULT '',
  tier TEXT NOT NULL DEFAULT 'B',
  published REAL NOT NULL,
  fetched REAL NOT NULL,
  matched TEXT NOT NULL DEFAULT '',
  score INTEGER,
  rumor INTEGER NOT NULL DEFAULT 0,
  tickers TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  scored_by TEXT NOT NULL DEFAULT '',
  pushed REAL,
  dup_of INTEGER,
  confirms INTEGER NOT NULL DEFAULT 0,
  event TEXT NOT NULL DEFAULT '',
  baseline INTEGER NOT NULL DEFAULT 0,
  UNIQUE(source, uid)
);
CREATE INDEX IF NOT EXISTS items_fetched ON items(fetched);
CREATE INDEX IF NOT EXISTS items_fp ON items(fp);
CREATE INDEX IF NOT EXISTS items_unscored ON items(score) WHERE score IS NULL;
CREATE TABLE IF NOT EXISTS watch (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'stock',
  symbols TEXT NOT NULL DEFAULT '[]',
  keywords TEXT NOT NULL DEFAULT '[]',
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chat (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, text TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS usage (day TEXT NOT NULL, kind TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0,
  tin INTEGER NOT NULL DEFAULT 0, tout INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(day, kind));
CREATE TABLE IF NOT EXISTS digests (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, slot TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL, title TEXT NOT NULL, data TEXT NOT NULL, since REAL NOT NULL DEFAULT 0, n_items INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS saved (id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT NOT NULL, kind TEXT NOT NULL,
  ref TEXT NOT NULL DEFAULT '', link TEXT NOT NULL DEFAULT '', ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memories (uid TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL, updated REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, origin TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS memories_updated ON memories(updated);
CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, name TEXT NOT NULL, thash TEXT NOT NULL,
  approved INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, last_seen REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS source_state (name TEXT PRIMARY KEY, ok REAL, err TEXT NOT NULL DEFAULT '',
  fails INTEGER NOT NULL DEFAULT 0, count INTEGER NOT NULL DEFAULT 0);
'''

MIGRATIONS = {
    'confirms': 'confirms INTEGER NOT NULL DEFAULT 0',
    'event': "event TEXT NOT NULL DEFAULT ''",
    'baseline': 'baseline INTEGER NOT NULL DEFAULT 0',
    'image_url': "image_url TEXT NOT NULL DEFAULT ''",   # 来源给的配图地址
    'image': "image TEXT NOT NULL DEFAULT ''",           # 缓存到本机的图片文件名（data/radar/img/）
    'img_state': 'img_state INTEGER NOT NULL DEFAULT 0', # 0 没试过 1 有图 2 没图/失败
    'saved': 'saved REAL',                               # 收进“仓库”的时间；收藏的永不过期
    'save_note': "save_note TEXT NOT NULL DEFAULT ''",
    'image_w': 'image_w INTEGER NOT NULL DEFAULT 0',
    'image_h': 'image_h INTEGER NOT NULL DEFAULT 0',
    'trimmed': 'trimmed INTEGER NOT NULL DEFAULT 0',     # 1 = 每日清理后只剩标题、来源和原网页链接
}

DEFAULT_SETTINGS = {
    'push_threshold': 4,        # 达到几分推送（1~5）
    'push_unwatched_min': 4,    # 不在关注列表里的消息，至少几分才推（用户选了“非关注的重要异动也推”）
    'max_push_per_hour': 12,
    'mute_until': 0,
    'llm_daily_batches': 400,   # 每天最多调用 DeepSeek 打分多少批（每批最多 20 条），超出后只用关键词规则
    'channels': ['ntfy', 'webpush'],   # 只有这两种推送渠道
    'digest_enabled': True,     # 每天三期日报（北京时间，都在 DeepSeek 非高峰时段）
    'digest_slots': ['07:30', '12:30', '21:00'],
}

CHANNELS = ('ntfy', 'webpush')

# 第一次启动时的默认关注（可在网页里聊天修改）
DEFAULT_WATCH = [
    ('英伟达', 'stock', ['NVDA'], ['英伟达', 'NVIDIA', 'Nvidia', '黄仁勋', 'Jensen Huang']),
    ('特斯拉', 'stock', ['TSLA'], ['特斯拉', 'Tesla', '马斯克', 'Musk']),
    ('比特币', 'crypto', ['BTC'], ['比特币', 'Bitcoin', 'BTC']),
    ('以太坊', 'crypto', ['ETH'], ['以太坊', 'Ethereum', 'ETH']),
]


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=15000')
    return db


@contextmanager
def opened(path: Optional[Path] = None):
    db = connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


def backup(keep: int = 7) -> str:
    """每天一份数据库备份（VACUUM INTO，运行中也是完整一致的副本），只保留最近 keep 份（只动 backups/ 里自己生成的文件）。"""
    d = config.DATA / 'backups'
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    target = d / f'radar-{bj_day()}.db'
    if target.exists():
        return ''
    tmp = target.with_suffix('.part')
    tmp.unlink(missing_ok=True)
    with opened() as db:
        db.execute('VACUUM INTO ?', (str(tmp),))
    os.chmod(tmp, 0o600)
    tmp.replace(target)
    olds = sorted(p for p in d.iterdir() if re.fullmatch(r'radar-\d{4}-\d{2}-\d{2}\.db', p.name))
    for p in olds[:-keep]:
        p.unlink(missing_ok=True)
    return target.name


def init(path: Optional[Path] = None) -> None:
    p = path or config.DB_PATH
    with opened(path) as db:
        db.executescript(SCHEMA)
        cols = {r[1] for r in db.execute('PRAGMA table_info(items)')}
        for col, ddl in MIGRATIONS.items():   # 旧数据库补新列
            if col not in cols:
                db.execute(f'ALTER TABLE items ADD COLUMN {ddl}')
        db.execute('CREATE INDEX IF NOT EXISTS items_event ON items(event)')
        db.execute('CREATE INDEX IF NOT EXISTS items_saved ON items(saved) WHERE saved IS NOT NULL')
        if not db.execute('SELECT 1 FROM settings WHERE k="initialized"').fetchone():
            now = time.time()
            for name, kind, symbols, keywords in DEFAULT_WATCH:
                db.execute('INSERT OR IGNORE INTO watch(name, kind, symbols, keywords, created) VALUES(?,?,?,?,?)',
                           (name, kind, json.dumps(symbols), json.dumps(keywords, ensure_ascii=False), now))
            db.execute('INSERT INTO settings VALUES("initialized", "1")')
    for f in (p, p.with_name(p.name + '-wal'), p.with_name(p.name + '-shm')):   # 聊天和记忆在库里，只给自己读
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass


# ---------- 设置 ----------
def get_settings(db: sqlite3.Connection) -> dict:
    out = dict(DEFAULT_SETTINGS)
    for row in db.execute('SELECT k, v FROM settings'):
        if row['k'] in out:
            try:
                out[row['k']] = json.loads(row['v'])
            except ValueError:
                pass
    out['channels'] = [c for c in out['channels'] if c in CHANNELS]   # 旧数据库里的 telegram 等一律忽略
    return out


def set_setting(db: sqlite3.Connection, key: str, value) -> None:
    if key not in DEFAULT_SETTINGS:
        raise KeyError(key)
    db.execute('INSERT OR REPLACE INTO settings VALUES(?, ?)', (key, json.dumps(value, ensure_ascii=False)))


# ---------- 关注列表 ----------
def watchlist(db: sqlite3.Connection) -> list:
    return [{'id': r['id'], 'name': r['name'], 'kind': r['kind'], 'symbols': json.loads(r['symbols']),
             'keywords': json.loads(r['keywords'])} for r in db.execute('SELECT * FROM watch ORDER BY id')]


def add_watch(db: sqlite3.Connection, name: str, kind: str, symbols: Iterable[str], keywords: Iterable[str]) -> None:
    symbols = sorted({s.strip().upper() for s in symbols if s and s.strip()})[:10]
    keywords = list(dict.fromkeys(k.strip() for k in [name, *keywords] if k and k.strip()))[:20]
    row = db.execute('SELECT symbols, keywords FROM watch WHERE name=?', (name,)).fetchone()
    if row:   # 已存在就合并
        symbols = sorted(set(json.loads(row['symbols'])) | set(symbols))
        keywords = list(dict.fromkeys(json.loads(row['keywords']) + keywords))
        db.execute('UPDATE watch SET kind=?, symbols=?, keywords=? WHERE name=?',
                   (kind, json.dumps(symbols), json.dumps(keywords, ensure_ascii=False), name))
    else:
        db.execute('INSERT INTO watch(name, kind, symbols, keywords, created) VALUES(?,?,?,?,?)',
                   (name, kind, json.dumps(symbols), json.dumps(keywords, ensure_ascii=False), time.time()))


def remove_watch(db: sqlite3.Connection, name: str) -> bool:
    return db.execute('DELETE FROM watch WHERE name=?', (name,)).rowcount > 0


# ---------- 资讯 ----------
def insert_item(db: sqlite3.Connection, item: dict) -> Optional[int]:
    cur = db.execute(
        'INSERT OR IGNORE INTO items(source, uid, fp, title, body, url, market, tier, published, fetched, matched, dup_of, baseline, image_url) '
        'VALUES(:source, :uid, :fp, :title, :body, :url, :market, :tier, :published, :fetched, :matched, :dup_of, :baseline, :image_url)',
        dict({'image_url': ''}, **item))
    return cur.lastrowid if cur.rowcount else None


def recent_items(db: sqlite3.Connection, limit: int = 80, min_score: int = 0, before: Optional[int] = None,
                 market: str = '', query: str = '') -> list:
    sql = 'SELECT * FROM items WHERE dup_of IS NULL AND COALESCE(score, 0) >= ?'
    args: list = [min_score]
    if before:
        sql += ' AND id < ?'
        args.append(before)
    if market:
        sql += ' AND market = ?'
        args.append(market)
    if query:
        sql += ' AND (title LIKE ? OR body LIKE ? OR matched LIKE ? OR tickers LIKE ?)'
        args += [f'%{query}%'] * 4
    sql += ' ORDER BY id DESC LIMIT ?'
    args.append(limit)
    return [dict(r) for r in db.execute(sql, args)]


def bj_day(ts: float = None) -> str:
    """北京时间的日期（DeepSeek 用量按北京时间算天，和日报、每日清理一致，不看机器时区）。"""
    return time.strftime('%Y-%m-%d', time.gmtime((ts or time.time()) + 8 * 3600))


def bump_usage(db: sqlite3.Connection, kind: str, tin: int = 0, tout: int = 0) -> None:
    day = bj_day()
    db.execute('INSERT INTO usage(day, kind, n, tin, tout) VALUES(?,?,1,?,?) '
               'ON CONFLICT(day, kind) DO UPDATE SET n=n+1, tin=tin+excluded.tin, tout=tout+excluded.tout',
               (day, kind, tin, tout))


def usage_today(db: sqlite3.Connection) -> dict:
    day = bj_day()
    return {r['kind']: {'n': r['n'], 'in': r['tin'], 'out': r['tout']}
            for r in db.execute('SELECT * FROM usage WHERE day=?', (day,))}


def source_result(db: sqlite3.Connection, name: str, error: str = '', count: int = 0) -> None:
    if error:
        db.execute('INSERT INTO source_state(name, err, fails) VALUES(?,?,1) '
                   'ON CONFLICT(name) DO UPDATE SET err=excluded.err, fails=fails+1', (name, error[:200]))
    else:
        db.execute('INSERT INTO source_state(name, ok, count) VALUES(?,?,?) '
                   'ON CONFLICT(name) DO UPDATE SET ok=excluded.ok, err="", fails=0, count=count+excluded.count',
                   (name, time.time(), count))


KEEP_DAYS = 30   # 没收藏的：第二天只剩标题、来源、原网页链接（见 daily_trim），30 天后整条删掉；收藏进“仓库”的永远完整保留


def daily_trim(db: sqlite3.Connection, now: float = None) -> int:
    """没收藏的消息，过了当天（北京时间 0 点前抓到的）就去掉正文、摘要、图片，只留标题、来源和原网页链接，
    以及去重/防重复推送要用的指纹、事件标识和分数。返回清理了几条。"""
    from datetime import datetime, timedelta, timezone
    bj = timezone(timedelta(hours=8))
    n = datetime.fromtimestamp(now or time.time(), bj)
    midnight = n.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return db.execute("UPDATE items SET body='', summary='', reason='', image='', image_url='', image_w=0, image_h=0, "
                      "img_state=2, trimmed=1 WHERE trimmed=0 AND saved IS NULL AND fetched < ?", (midnight,)).rowcount


def prune(db: sqlite3.Connection, days: int = KEEP_DAYS) -> None:
    db.execute('DELETE FROM items WHERE fetched < ? AND saved IS NULL', (time.time() - days * 86400,))
    db.execute('DELETE FROM chat WHERE id NOT IN (SELECT id FROM chat ORDER BY id DESC LIMIT 200)')
    db.execute('DELETE FROM digests WHERE created < ?', (time.time() - 90 * 86400,))


def mark_saved(db: sqlite3.Connection, target: str, kind: str, ref: str, link: str = '') -> None:
    """记一笔“存到 Notion / 发到 Telegram 群”，网页上显示“已存”。"""
    db.execute('INSERT INTO saved(target, kind, ref, link, ts) VALUES(?,?,?,?,?)', (target, kind, ref, link, time.time()))


def saved_refs(db: sqlite3.Connection, target: str, kind: str, refs: Iterable[str]) -> dict:
    refs = [str(r) for r in refs]
    if not refs:
        return {}
    marks = ','.join('?' * len(refs))
    return {r['ref']: r['link'] for r in db.execute(
        f'SELECT ref, link FROM saved WHERE target=? AND kind=? AND ref IN ({marks})', [target, kind, *refs])}
