"""SQLite 持久化：讨论（debate）、每轮每个模型的输出、Council State、FINAL、聊天设置、模型状态。

程序崩溃或 systemd 重启后，从这里恢复：status='running' 或 'queued' 的讨论会从最后保存的轮次继续。
完整历史只存在数据库里，交给模型的是精简的 Council State（见 orchestrator/debate.py）。
数据库文件权限 600（进程 umask 077），位置见 config.DATA_DIR（Mini PC 上是 ~/ai-council/data/council.db）。
"""
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'compact',
    default_rounds INTEGER,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS debates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    source_message_id INTEGER,
    question TEXT NOT NULL,
    participants TEXT NOT NULL,
    total_rounds INTEGER NOT NULL,
    current_round INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,              -- queued / running / done / cancelled / error
    mode TEXT NOT NULL DEFAULT 'compact',
    force_final INTEGER NOT NULL DEFAULT 0,
    state_json TEXT NOT NULL DEFAULT '{}',
    final_answer TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL,
    UNIQUE (chat_id, source_message_id)
);
CREATE INDEX IF NOT EXISTS debates_status ON debates(status);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debate_id INTEGER NOT NULL REFERENCES debates(id),
    round INTEGER NOT NULL,
    model TEXT NOT NULL,
    kind TEXT NOT NULL,                -- answer / state / final
    content TEXT,
    ok INTEGER NOT NULL,
    error_type TEXT,
    latency_ms INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_debate ON turns(debate_id, round);
CREATE TABLE IF NOT EXISTS model_status (
    model TEXT PRIMARY KEY,
    online INTEGER NOT NULL,
    reason TEXT,
    updated_at REAL NOT NULL
);
"""

ACTIVE = ('queued', 'running')


@dataclass
class DebateRow:
    id: int
    chat_id: int
    source_message_id: Optional[int]
    question: str
    participants: list
    total_rounds: int
    current_round: int
    status: str
    mode: str
    force_final: bool
    state: dict
    final_answer: Optional[str]
    error: Optional[str]
    created_at: float
    updated_at: float


class Store:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        if str(path) != ':memory:':
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10, isolation_level=None)   # autocommit
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ---------- 聊天设置 ----------
    def chat_settings(self, chat_id: int) -> tuple[str, Optional[int]]:
        row = self.db.execute('SELECT mode, default_rounds FROM chats WHERE chat_id=?', (chat_id,)).fetchone()
        return (row['mode'], row['default_rounds']) if row else ('compact', None)

    def set_mode(self, chat_id: int, mode: str) -> None:
        self.db.execute('INSERT INTO chats(chat_id, mode, updated_at) VALUES(?,?,?) '
                        'ON CONFLICT(chat_id) DO UPDATE SET mode=excluded.mode, updated_at=excluded.updated_at',
                        (chat_id, mode, self.clock()))

    def set_default_rounds(self, chat_id: int, rounds: int) -> None:
        self.db.execute('INSERT INTO chats(chat_id, default_rounds, updated_at) VALUES(?,?,?) '
                        'ON CONFLICT(chat_id) DO UPDATE SET default_rounds=excluded.default_rounds, '
                        'updated_at=excluded.updated_at', (chat_id, rounds, self.clock()))

    # ---------- 讨论 ----------
    def create_debate(self, chat_id: int, question: str, participants: list, rounds: int, mode: str,
                      source_message_id: Optional[int] = None) -> Optional[int]:
        """新建排队中的讨论；同一条 Telegram 消息重复投递时返回 None（防止重复执行）。"""
        now = self.clock()
        try:
            cur = self.db.execute(
                'INSERT INTO debates(chat_id, source_message_id, question, participants, total_rounds, status, mode, '
                'created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (chat_id, source_message_id, question, json.dumps(participants), rounds, 'queued', mode, now, now))
        except sqlite3.IntegrityError:
            return None
        return cur.lastrowid

    def _row(self, row) -> Optional[DebateRow]:
        if row is None:
            return None
        return DebateRow(row['id'], row['chat_id'], row['source_message_id'], row['question'],
                         json.loads(row['participants']), row['total_rounds'], row['current_round'], row['status'],
                         row['mode'], bool(row['force_final']), json.loads(row['state_json'] or '{}'),
                         row['final_answer'], row['error'], row['created_at'], row['updated_at'])

    def get(self, debate_id: int) -> Optional[DebateRow]:
        return self._row(self.db.execute('SELECT * FROM debates WHERE id=?', (debate_id,)).fetchone())

    def active_for_chat(self, chat_id: int) -> Optional[DebateRow]:
        return self._row(self.db.execute(
            "SELECT * FROM debates WHERE chat_id=? AND status IN ('queued','running') ORDER BY id LIMIT 1",
            (chat_id,)).fetchone())

    def last_for_chat(self, chat_id: int) -> Optional[DebateRow]:
        return self._row(self.db.execute('SELECT * FROM debates WHERE chat_id=? ORDER BY id DESC LIMIT 1',
                                         (chat_id,)).fetchone())

    def unfinished(self) -> list:
        """重启后要继续的讨论（按创建顺序）。"""
        rows = self.db.execute("SELECT * FROM debates WHERE status IN ('queued','running') ORDER BY id").fetchall()
        return [self._row(r) for r in rows]

    def queue_position(self, debate_id: int) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM debates WHERE status IN ('queued','running') AND id < ?",
                              (debate_id,)).fetchone()
        return int(row[0])

    def update(self, debate_id: int, **fields) -> None:
        if 'state' in fields:
            fields['state_json'] = json.dumps(fields.pop('state'), ensure_ascii=False)
        if 'participants' in fields:
            fields['participants'] = json.dumps(fields['participants'])
        if 'force_final' in fields:
            fields['force_final'] = int(bool(fields['force_final']))
        fields['updated_at'] = self.clock()
        if fields.get('status') in ('done', 'cancelled', 'error'):
            fields['finished_at'] = self.clock()
        cols = ', '.join(f'{k}=?' for k in fields)
        self.db.execute(f'UPDATE debates SET {cols} WHERE id=?', (*fields.values(), debate_id))

    def add_rounds(self, debate_id: int, extra: int, max_rounds: int) -> int:
        row = self.get(debate_id)
        total = min(max_rounds, row.total_rounds + extra)
        self.update(debate_id, total_rounds=total, force_final=False)
        return total

    # ---------- 每轮输出 ----------
    def add_turn(self, debate_id: int, round_no: int, model: str, kind: str, content: Optional[str], ok: bool,
                 error_type: Optional[str] = None, latency_ms: Optional[int] = None, usage: Optional[dict] = None) -> None:
        usage = usage or {}
        self.db.execute(
            'INSERT INTO turns(debate_id, round, model, kind, content, ok, error_type, latency_ms, tokens_in, tokens_out, '
            'created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (debate_id, round_no, model, kind, content, int(ok), error_type, latency_ms,
             usage.get('prompt_tokens'), usage.get('completion_tokens'), self.clock()))

    def turns(self, debate_id: int, round_no: Optional[int] = None) -> list:
        sql, args = 'SELECT * FROM turns WHERE debate_id=?', [debate_id]
        if round_no is not None:
            sql += ' AND round=?'
            args.append(round_no)
        return [dict(r) for r in self.db.execute(sql + ' ORDER BY id', args).fetchall()]

    def usage_since(self, since: float) -> dict:
        """各模型自 since 以来在讨论里的调用次数、失败次数和 token（给日报和 /status 用）。"""
        out = {}
        for r in self.db.execute('SELECT model, COUNT(*) n, SUM(1-ok) failed, COALESCE(SUM(tokens_in),0) tin, '
                                 'COALESCE(SUM(tokens_out),0) tout FROM turns WHERE created_at>=? GROUP BY model',
                                 (since,)).fetchall():
            out[r['model']] = {'calls': r['n'], 'failed': r['failed'], 'tokens_in': r['tin'], 'tokens_out': r['tout']}
        return out

    def debates_since(self, since: float) -> dict:
        rows = self.db.execute('SELECT status, COUNT(*) n FROM debates WHERE created_at>=? GROUP BY status',
                               (since,)).fetchall()
        return {r['status']: r['n'] for r in rows}

    # ---------- 模型在线状态 ----------
    def set_model_status(self, model: str, online: bool, reason: str = '') -> bool:
        """返回状态是否发生了变化（变化时才通知主人，避免刷屏）。"""
        row = self.db.execute('SELECT online FROM model_status WHERE model=?', (model,)).fetchone()
        # 第一次记录时只有“离线”算变化；之后在线/离线切换才算
        changed = (not online) if row is None else bool(row['online']) != online
        self.db.execute('INSERT INTO model_status(model, online, reason, updated_at) VALUES(?,?,?,?) '
                        'ON CONFLICT(model) DO UPDATE SET online=excluded.online, reason=excluded.reason, '
                        'updated_at=excluded.updated_at', (model, int(online), reason, self.clock()))
        return changed

    def model_status(self) -> dict:
        return {r['model']: {'online': bool(r['online']), 'reason': r['reason'], 'updated_at': r['updated_at']}
                for r in self.db.execute('SELECT * FROM model_status').fetchall()}
