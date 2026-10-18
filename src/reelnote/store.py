import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  code TEXT PRIMARY KEY, inbox_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, note_path TEXT, data TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS embeddings (code TEXT PRIMARY KEY, vec BLOB NOT NULL, hub TEXT);
CREATE TABLE IF NOT EXISTS cooldowns (provider TEXT PRIMARY KEY, until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass
class Item:
    code: str
    inbox_id: str
    state: str
    attempts: int
    last_error: str | None
    note_path: str | None
    data: dict = field(default_factory=dict)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def _row(self, r) -> Item:
        return Item(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6]))

    def get(self, code: str) -> Item | None:
        r = self.db.execute("SELECT code,inbox_id,state,attempts,last_error,note_path,data FROM items WHERE code=?",
                            (code,)).fetchone()
        return self._row(r) if r else None

    def create(self, code: str, inbox_id: str, data: dict) -> Item:
        self.db.execute("INSERT INTO items(code,inbox_id,data) VALUES(?,?,?)",
                        (code, inbox_id, json.dumps(data, ensure_ascii=False)))
        self.db.commit()
        return self.get(code)

    def update(self, code: str, *, state: str | None = None, data: dict | None = None,
               note_path: str | None = None) -> Item:
        item = self.get(code)
        merged = {**item.data, **(data or {})}
        self.db.execute("UPDATE items SET state=?, data=?, note_path=? WHERE code=?",
                        (state or item.state, json.dumps(merged, ensure_ascii=False),
                         note_path or item.note_path, code))
        self.db.commit()
        return self.get(code)

    def fail_attempt(self, code: str, error: str, max_attempts: int) -> Item:
        item = self.get(code)
        attempts = item.attempts + 1
        state = "failed" if attempts >= max_attempts else item.state
        self.db.execute("UPDATE items SET attempts=?, last_error=?, state=? WHERE code=?",
                        (attempts, error, state, code))
        self.db.commit()
        return self.get(code)

    def reset(self, code: str) -> Item:
        self.db.execute("UPDATE items SET attempts=0, last_error=NULL, state='queued' WHERE code=?", (code,))
        self.db.commit()
        return self.get(code)

    def by_state(self, state: str) -> list[Item]:
        rows = self.db.execute("SELECT code,inbox_id,state,attempts,last_error,note_path,data FROM items "
                               "WHERE state=? ORDER BY rowid", (state,)).fetchall()
        return [self._row(r) for r in rows]

    def all(self) -> list[Item]:
        rows = self.db.execute("SELECT code,inbox_id,state,attempts,last_error,note_path,data FROM items "
                               "ORDER BY rowid").fetchall()
        return [self._row(r) for r in rows]

    def put_embedding(self, code: str, vec: list[float]) -> None:
        blob = np.asarray(vec, dtype=np.float32).tobytes()
        self.db.execute("INSERT INTO embeddings(code,vec) VALUES(?,?) ON CONFLICT(code) DO UPDATE SET vec=excluded.vec",
                        (code, blob))
        self.db.commit()

    def embeddings(self) -> list[tuple[str, list[float], str | None]]:
        rows = self.db.execute("SELECT code,vec,hub FROM embeddings ORDER BY rowid").fetchall()
        return [(c, np.frombuffer(v, dtype=np.float32).tolist(), h) for c, v, h in rows]

    def set_hub(self, code: str, hub: str | None) -> None:
        self.db.execute("UPDATE embeddings SET hub=? WHERE code=?", (hub, code))
        self.db.commit()

    def cooldown_until(self, provider: str) -> float:
        r = self.db.execute("SELECT until FROM cooldowns WHERE provider=?", (provider,)).fetchone()
        return r[0] if r else 0.0

    def set_cooldown(self, provider: str, until: float) -> None:
        self.db.execute("INSERT INTO cooldowns VALUES(?,?) ON CONFLICT(provider) DO UPDATE SET until=excluded.until",
                        (provider, until))
        self.db.commit()

    def get_meta(self, key: str, default=None):
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_meta(self, key: str, value) -> None:
        self.db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value, ensure_ascii=False)))
        self.db.commit()
