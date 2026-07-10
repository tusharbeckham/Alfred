"""Megamind - a fast, local, offline SQLite store for Alfred's memory.

A genuine local desktop database (SQLite + FTS5 full-text index) backing Alfred's memory:
indexed full-text recall + recency, migrated from memory/memory.jsonl. Pure Python stdlib
(`sqlite3`) - no dependencies, fully offline.

Usage:
  python scripts/megamind.py init                                   # create DB + migrate memory.jsonl
  python scripts/megamind.py add -T decision -o "topic" -x "text" -g tag1,tag2
  python scripts/megamind.py recall -q "query" -k 5
  python scripts/megamind.py stats
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "memory" / "megamind.db"
JSONL = ROOT / "memory" / "memory.jsonl"


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=5.0)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA busy_timeout=5000;")
    return con


def init(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS memories("
        "id INTEGER PRIMARY KEY, ts REAL, type TEXT, topic TEXT, text TEXT, tags TEXT)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_mem_ts ON memories(ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type)")
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5("
        "topic, text, tags, content='memories', content_rowid='id')"
    )
    con.execute(
        "CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN "
        "INSERT INTO mem_fts(rowid, topic, text, tags) VALUES (new.id, new.topic, new.text, new.tags); END"
    )
    con.commit()


def add(con: sqlite3.Connection, mtype: str, topic: str, text: str, tags: str = "") -> int:
    cur = con.execute(
        "INSERT INTO memories(ts,type,topic,text,tags) VALUES(?,?,?,?,?)",
        (time.time(), mtype, topic, text, tags),
    )
    con.commit()
    return int(cur.lastrowid)


def migrate(con: sqlite3.Connection) -> int:
    """Import memory.jsonl entries not already stored (idempotent by exact text)."""
    if not JSONL.exists():
        return 0
    existing = {r[0] for r in con.execute("SELECT text FROM memories")}
    added = 0
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = (e.get("text") or "").strip()
        if not text or text in existing:
            continue
        tags = e.get("tags", "")
        if isinstance(tags, list):
            tags = ",".join(str(t) for t in tags)
        add(con, e.get("type", "note"), e.get("topic", ""), text, tags or "")
        existing.add(text)
        added += 1
    return added


def _fts_query(query: str) -> str:
    """Forgiving FTS5 query: OR the meaningful terms so partial matches still rank via bm25."""
    terms = [t for t in re.findall(r"[A-Za-z0-9]+", (query or "").lower()) if len(t) > 2]
    return " OR ".join(terms)


def recall(con: sqlite3.Connection, query: str, k: int = 5):
    """Fast recall: FTS5 (bm25-ranked, OR-of-terms) when a query is given, else most-recent. Returns (rows, ms)."""
    t0 = time.perf_counter()
    fq = _fts_query(query)
    recency = "SELECT ts,type,topic,text,tags FROM memories ORDER BY ts DESC LIMIT ?"
    if fq:
        try:
            rows = con.execute(
                "SELECT m.ts,m.type,m.topic,m.text,m.tags FROM mem_fts f "
                "JOIN memories m ON m.id=f.rowid WHERE mem_fts MATCH ? "
                "ORDER BY bm25(mem_fts), m.ts DESC LIMIT ?",
                (fq, k),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = con.execute(recency, (k,)).fetchall()
    else:
        rows = con.execute(recency, (k,)).fetchall()
    return rows, (time.perf_counter() - t0) * 1000.0


def main() -> None:
    p = argparse.ArgumentParser(description="Alfred megamind SQLite memory store")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    a = sub.add_parser("add")
    a.add_argument("-T", "--type", default="note")
    a.add_argument("-o", "--topic", default="")
    a.add_argument("-x", "--text", required=True)
    a.add_argument("-g", "--tags", default="")
    r = sub.add_parser("recall")
    r.add_argument("-q", "--query", default="")
    r.add_argument("-k", "--k", type=int, default=5)
    sub.add_parser("stats")
    args = p.parse_args()

    con = connect()
    init(con)
    if args.cmd == "init":
        n = migrate(con)
        total = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        print(f"megamind.db ready at {DB}  |  migrated {n} new  |  total {total} memories")
    elif args.cmd == "add":
        print(f"added #{add(con, args.type, args.topic, args.text, args.tags)}")
    elif args.cmd == "recall":
        rows, ms = recall(con, args.query, args.k)
        print(f"[{len(rows)} results in {ms:.2f} ms]")
        for _ts, typ, topic, text, _tags in rows:
            print(f"- ({typ}) {topic}: {text[:160]}")
    elif args.cmd == "stats":
        total = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        print(f"megamind: {total} memories in {DB}")


if __name__ == "__main__":
    main()
