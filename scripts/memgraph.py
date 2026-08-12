#!/usr/bin/env python3
"""Alfred memory graph - bi-temporal facts over SQLite.

WHY THIS EXISTS
---------------
`megamind.db` already gives fast keyword recall of *episodes*. What it cannot do
is answer **"what is true NOW, and what changed?"**, because appending never
retracts. So "the Owner prefers X" stays recallable forever after the Owner
switches to Y, and the assistant confidently contradicts itself.

This module adds the missing layer: facts with **two independent timelines**,
following the model Zep/Graphiti describe (arxiv 2501.13956):

  * **world time**  - `t_valid` / `t_invalid`: when the fact was true in the world
  * **system time** - `t_created` / `t_expired`: when Alfred learned / retired it

When a new fact contradicts an old one, the old edge is **invalidated, not
deleted** - `t_invalid` is set to the new fact's `t_valid`. History is preserved,
so both questions are answerable: "what is true now" AND "when did it change".

DESIGN CONSTRAINTS
------------------
* **Additive.** New `mg_*` tables in the existing `memory/megamind.db`. Nothing
  existing is altered or dropped; `megamind.py` keeps working untouched.
* **Zero hard dependencies.** Standard library only. `sqlite-vec` is used as an
  optional accelerator if installed, and embeddings are optional: with no vectors
  the graph still works on FTS5 (BM25) + traversal. This matters because LM Studio
  is frequently offline, and a memory system that needs a model to recall is not
  a memory system.
* **Provenance is mandatory.** Every fact cites the episode that produced it. A
  fact with no episode is a bug, not a warning - Alfred must never fabricate.
* **Retrieval is token-bounded.** Recall returns a budgeted context block, never
  "everything relevant". Unbounded recall is how a context window dies.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "memory" / "megamind.db"

#: Fact subtypes Alfred specifically needs (see the graph plan, section 3.2).
FACT_KINDS = ("fact", "decision", "preference", "outcome", "learning")

#: A rough chars-per-token figure used only to *bound* recall. Deliberately
#: pessimistic: over-estimating tokens truncates early, which is the safe error.
CHARS_PER_TOKEN = 4


class MemoryGraphError(RuntimeError):
    """A fact or query is malformed."""


# ------------------------------------------------------------------ connection


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(path) if path else DB
    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(target), timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA busy_timeout=5000;")
    con.execute("PRAGMA foreign_keys=ON;")
    return con


def init(con: sqlite3.Connection) -> None:
    """Create the graph tables. Safe to run repeatedly."""
    con.executescript(
        """
        -- Raw provenance. Never rewritten; every derived fact points here.
        CREATE TABLE IF NOT EXISTS mg_episode(
            id        INTEGER PRIMARY KEY,
            ts        REAL    NOT NULL,
            source    TEXT    NOT NULL,          -- 'session' | 'tool' | 'jsonl' | ...
            kind      TEXT    NOT NULL DEFAULT 'text',
            text      TEXT    NOT NULL,
            ref       TEXT                        -- run id, file path, url
        );

        -- Entities: projects, files, agents, models, people, concepts.
        -- Identity is the NAME alone. Keying on (name, kind) fragments identity:
        -- "sqlite" mentioned as a tool and "sqlite" mentioned as a concept would
        -- become two unrelated nodes, and graph traversal would silently break at
        -- the join. `kind` is therefore a refinable label, not part of identity.
        CREATE TABLE IF NOT EXISTS mg_entity(
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE,
            kind       TEXT NOT NULL DEFAULT 'concept',
            summary    TEXT NOT NULL DEFAULT '',
            embedding  BLOB,
            created_ts REAL NOT NULL,
            updated_ts REAL NOT NULL
        );

        -- Facts are the bi-temporal core. Subject and object are entities; an
        -- object may instead be a literal (object_text) for values like "dark".
        CREATE TABLE IF NOT EXISTS mg_fact(
            id            INTEGER PRIMARY KEY,
            kind          TEXT    NOT NULL DEFAULT 'fact',
            subject_id    INTEGER NOT NULL REFERENCES mg_entity(id),
            predicate     TEXT    NOT NULL,
            object_id     INTEGER REFERENCES mg_entity(id),
            object_text   TEXT    NOT NULL DEFAULT '',
            statement     TEXT    NOT NULL,
            confidence    REAL    NOT NULL DEFAULT 1.0,
            episode_id    INTEGER NOT NULL REFERENCES mg_episode(id),
            -- world time
            t_valid       REAL    NOT NULL,
            t_invalid     REAL,
            -- system time
            t_created     REAL    NOT NULL,
            t_expired     REAL,
            superseded_by INTEGER REFERENCES mg_fact(id),
            embedding     BLOB
        );

        CREATE INDEX IF NOT EXISTS idx_mg_fact_sp
            ON mg_fact(subject_id, predicate);
        CREATE INDEX IF NOT EXISTS idx_mg_fact_live
            ON mg_fact(t_invalid);
        CREATE INDEX IF NOT EXISTS idx_mg_fact_kind
            ON mg_fact(kind);
        CREATE INDEX IF NOT EXISTS idx_mg_fact_obj
            ON mg_fact(object_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS mg_fact_fts USING fts5(
            statement, predicate, content='mg_fact', content_rowid='id'
        );
        CREATE TRIGGER IF NOT EXISTS mg_fact_ai AFTER INSERT ON mg_fact BEGIN
            INSERT INTO mg_fact_fts(rowid, statement, predicate)
            VALUES (new.id, new.statement, new.predicate);
        END;
        CREATE TRIGGER IF NOT EXISTS mg_fact_ad AFTER DELETE ON mg_fact BEGIN
            INSERT INTO mg_fact_fts(mg_fact_fts, rowid, statement, predicate)
            VALUES ('delete', old.id, old.statement, old.predicate);
        END;
        CREATE TRIGGER IF NOT EXISTS mg_fact_au AFTER UPDATE OF statement, predicate ON mg_fact BEGIN
            INSERT INTO mg_fact_fts(mg_fact_fts, rowid, statement, predicate)
            VALUES ('delete', old.id, old.statement, old.predicate);
            INSERT INTO mg_fact_fts(rowid, statement, predicate)
            VALUES (new.id, new.statement, new.predicate);
        END;
        """
    )
    con.commit()


# ------------------------------------------------------------------- embeddings


def pack_embedding(vector: Sequence[float] | None) -> bytes | None:
    """Store float32 little-endian. Compatible with sqlite-vec's blob format, so
    an index can be added later without rewriting stored vectors."""
    if not vector:
        return None
    return struct.pack(f"<{len(vector)}f", *(float(x) for x in vector))


def unpack_embedding(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


#: LM Studio's OpenAI-compatible endpoint. Embeddings are OPTIONAL throughout: if
#: this is unreachable the graph still answers on FTS5 + traversal, which is why
#: recall never hard-depends on a model being loaded.
EMBED_URL = os.environ.get("ALFRED_EMBED_URL", "http://localhost:1234/v1/embeddings")
EMBED_MODEL = os.environ.get("ALFRED_EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5")


def embed(text: str, *, url: str | None = None, model: str | None = None,
          timeout: float = 30.0) -> list[float]:
    """Embed text via the local model. Returns [] when unavailable - never raises,
    because a memory system that cannot recall while a model is down is useless."""
    import urllib.error
    import urllib.request

    payload = json.dumps({"model": model or EMBED_MODEL, "input": text[:8000]}).encode("utf-8")
    request = urllib.request.Request(
        url or EMBED_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read())
        vector = body["data"][0]["embedding"]
        return [float(x) for x in vector]
    except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError, TypeError):
        return []


def embed_available(*, timeout: float = 4.0) -> bool:
    return bool(embed("ping", timeout=timeout))


def backfill_embeddings(con: sqlite3.Connection, *, limit: int | None = None,
                        progress=None) -> dict[str, int]:
    """Embed facts that have no vector yet. Idempotent and interruptible.

    Vectors only *improve* ranking - they are fused with BM25 by RRF, never
    required - so a partial backfill is a valid state rather than a broken one.
    """
    rows = list(con.execute(
        "SELECT id, statement FROM mg_fact WHERE embedding IS NULL"
        + (f" LIMIT {int(limit)}" if limit else "")
    ))
    if not rows:
        return {"embedded": 0, "skipped": 0, "remaining": 0}
    if not embed_available():
        return {"embedded": 0, "skipped": len(rows), "remaining": len(rows),
                "note": "embedding endpoint unavailable"}

    done = failed = 0
    for index, row in enumerate(rows, start=1):
        vector = embed(row["statement"] or "")
        if not vector:
            failed += 1
            continue
        con.execute("UPDATE mg_fact SET embedding=? WHERE id=?",
                    (pack_embedding(vector), row["id"]))
        done += 1
        if done % 10 == 0:
            con.commit()
        if progress:
            progress(index, len(rows))
    con.commit()
    return {"embedded": done, "skipped": failed,
            "remaining": int(con.execute(
                "SELECT COUNT(*) FROM mg_fact WHERE embedding IS NULL").fetchone()[0])}


# --------------------------------------------------------------------- writing


def add_episode(con: sqlite3.Connection, text: str, source: str = "session",
                kind: str = "text", ref: str | None = None,
                ts: float | None = None) -> int:
    if not (text or "").strip():
        raise MemoryGraphError("an episode needs text - it is the provenance record")
    cur = con.execute(
        "INSERT INTO mg_episode(ts, source, kind, text, ref) VALUES(?,?,?,?,?)",
        (ts if ts is not None else time.time(), source, kind, text, ref),
    )
    con.commit()
    return int(cur.lastrowid)


def upsert_entity(con: sqlite3.Connection, name: str, kind: str = "concept",
                  summary: str | None = None,
                  embedding: Sequence[float] | None = None) -> int:
    """Resolve an entity by NAME, refining its kind and evolving summary.

    Resolution is by name alone so the same real-world thing is one node however
    it happens to be mentioned. A specific kind upgrades the default 'concept'
    label, but never splits the entity in two.
    """
    name = (name or "").strip()
    if not name:
        raise MemoryGraphError("an entity needs a name")
    now = time.time()
    row = con.execute("SELECT id, kind FROM mg_entity WHERE name=?", (name,)).fetchone()
    if row:
        # Upgrade a placeholder kind to a specific one; never downgrade.
        better_kind = kind if (kind != "concept" and row["kind"] == "concept") else None
        if summary or embedding or better_kind:
            con.execute(
                "UPDATE mg_entity SET summary=COALESCE(?,summary), "
                "embedding=COALESCE(?,embedding), kind=COALESCE(?,kind), updated_ts=? WHERE id=?",
                (summary, pack_embedding(embedding), better_kind, now, row["id"]),
            )
            con.commit()
        return int(row["id"])
    cur = con.execute(
        "INSERT INTO mg_entity(name, kind, summary, embedding, created_ts, updated_ts) "
        "VALUES(?,?,?,?,?,?)",
        (name, kind, summary or "", pack_embedding(embedding), now, now),
    )
    con.commit()
    return int(cur.lastrowid)


@dataclass
class Assertion:
    """The outcome of asserting a fact, so callers can see what it displaced."""

    fact_id: int
    created: bool
    invalidated: list[int] = field(default_factory=list)
    duplicate_of: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "factId": self.fact_id, "created": self.created,
            "invalidated": self.invalidated, "duplicateOf": self.duplicate_of,
        }


def assert_fact(
    con: sqlite3.Connection,
    subject: str,
    predicate: str,
    obj: str,
    *,
    episode_id: int,
    kind: str = "fact",
    statement: str | None = None,
    subject_kind: str = "concept",
    object_kind: str | None = None,
    confidence: float = 1.0,
    t_valid: float | None = None,
    single_valued: bool = True,
    embedding: Sequence[float] | None = None,
) -> Assertion:
    """Assert a fact, invalidating anything it contradicts.

    ``single_valued`` marks a predicate that can hold exactly one value at a time
    (a preference, a current model, a status). Asserting a new value **invalidates
    the previous one** rather than appending a second contradictory truth. Set it
    False for genuinely multi-valued predicates such as "depends_on".

    Nothing is ever deleted: the superseded fact keeps its `t_valid`, gains a
    `t_invalid`, and records `superseded_by`, so history stays queryable.
    """
    predicate = (predicate or "").strip()
    if not predicate:
        raise MemoryGraphError("a fact needs a predicate")
    if kind not in FACT_KINDS:
        raise MemoryGraphError(f"unknown fact kind {kind!r}; expected one of {FACT_KINDS}")
    if not 0.0 <= float(confidence) <= 1.0:
        raise MemoryGraphError("confidence must be between 0 and 1")
    if not con.execute("SELECT 1 FROM mg_episode WHERE id=?", (episode_id,)).fetchone():
        # Provenance is not optional. A fact with no source is fabrication.
        raise MemoryGraphError(f"episode {episode_id} does not exist; every fact needs provenance")

    now = time.time()
    valid_from = t_valid if t_valid is not None else now
    subject_id = upsert_entity(con, subject, subject_kind)
    object_id = upsert_entity(con, obj, object_kind) if object_kind else None
    object_text = "" if object_id else (obj or "")
    text = statement or f"{subject} {predicate} {obj}".strip()

    # An identical live fact is a repeat observation, not a change.
    live = _live_facts(con, subject_id, predicate)
    for row in live:
        same_object = (row["object_id"] == object_id) and (row["object_text"] == object_text)
        if same_object:
            return Assertion(fact_id=int(row["id"]), created=False, duplicate_of=int(row["id"]))

    cur = con.execute(
        "INSERT INTO mg_fact(kind, subject_id, predicate, object_id, object_text, statement,"
        " confidence, episode_id, t_valid, t_invalid, t_created, t_expired, embedding)"
        " VALUES(?,?,?,?,?,?,?,?,?,NULL,?,NULL,?)",
        (kind, subject_id, predicate, object_id, object_text, text, float(confidence),
         episode_id, valid_from, now, pack_embedding(embedding)),
    )
    new_id = int(cur.lastrowid)

    invalidated: list[int] = []
    if single_valued:
        for row in live:
            # World time: it stopped being true when the new fact became true.
            # System time: we learned that now.
            con.execute(
                "UPDATE mg_fact SET t_invalid=?, t_expired=?, superseded_by=? WHERE id=?",
                (valid_from, now, new_id, row["id"]),
            )
            invalidated.append(int(row["id"]))
    con.commit()
    return Assertion(fact_id=new_id, created=True, invalidated=invalidated)


def retract_fact(con: sqlite3.Connection, fact_id: int, *, t_invalid: float | None = None) -> bool:
    """Mark a fact no longer true without deleting it."""
    now = time.time()
    cur = con.execute(
        "UPDATE mg_fact SET t_invalid=?, t_expired=? WHERE id=? AND t_invalid IS NULL",
        (t_invalid if t_invalid is not None else now, now, fact_id),
    )
    con.commit()
    return cur.rowcount > 0


def _live_facts(con: sqlite3.Connection, subject_id: int, predicate: str) -> list[sqlite3.Row]:
    return list(con.execute(
        "SELECT * FROM mg_fact WHERE subject_id=? AND predicate=? AND t_invalid IS NULL",
        (subject_id, predicate),
    ))


# --------------------------------------------------------------------- reading


def _row_to_fact(con: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    subject = con.execute("SELECT name FROM mg_entity WHERE id=?", (row["subject_id"],)).fetchone()
    obj = None
    if row["object_id"]:
        obj = con.execute("SELECT name FROM mg_entity WHERE id=?", (row["object_id"],)).fetchone()
    return {
        "id": int(row["id"]),
        "kind": row["kind"],
        "subject": subject["name"] if subject else None,
        "predicate": row["predicate"],
        "object": (obj["name"] if obj else row["object_text"]),
        "statement": row["statement"],
        "confidence": row["confidence"],
        "episodeId": int(row["episode_id"]),
        "tValid": row["t_valid"],
        "tInvalid": row["t_invalid"],
        "tCreated": row["t_created"],
        "tExpired": row["t_expired"],
        "supersededBy": row["superseded_by"],
        "live": row["t_invalid"] is None,
    }


def current(con: sqlite3.Connection, subject: str | None = None,
            predicate: str | None = None, kind: str | None = None,
            limit: int = 50) -> list[dict[str, Any]]:
    """Facts true right now."""
    sql = ("SELECT f.* FROM mg_fact f JOIN mg_entity e ON e.id=f.subject_id "
           "WHERE f.t_invalid IS NULL")
    params: list[Any] = []
    if subject:
        sql += " AND e.name=?"
        params.append(subject)
    if predicate:
        sql += " AND f.predicate=?"
        params.append(predicate)
    if kind:
        sql += " AND f.kind=?"
        params.append(kind)
    sql += " ORDER BY f.t_valid DESC LIMIT ?"
    params.append(limit)
    return [_row_to_fact(con, r) for r in con.execute(sql, params)]


def history(con: sqlite3.Connection, subject: str, predicate: str) -> list[dict[str, Any]]:
    """Every version of a fact, oldest first - the "when did it change" answer."""
    rows = con.execute(
        "SELECT f.* FROM mg_fact f JOIN mg_entity e ON e.id=f.subject_id "
        "WHERE e.name=? AND f.predicate=? ORDER BY f.t_valid ASC, f.id ASC",
        (subject, predicate),
    )
    return [_row_to_fact(con, r) for r in rows]


def as_of(con: sqlite3.Connection, when: float, subject: str | None = None,
          predicate: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """What was true in the world at `when` (world time, not system time)."""
    sql = ("SELECT f.* FROM mg_fact f JOIN mg_entity e ON e.id=f.subject_id "
           "WHERE f.t_valid <= ? AND (f.t_invalid IS NULL OR f.t_invalid > ?)")
    params: list[Any] = [when, when]
    if subject:
        sql += " AND e.name=?"
        params.append(subject)
    if predicate:
        sql += " AND f.predicate=?"
        params.append(predicate)
    sql += " ORDER BY f.t_valid DESC LIMIT ?"
    params.append(limit)
    return [_row_to_fact(con, r) for r in con.execute(sql, params)]


def episode(con: sqlite3.Connection, episode_id: int) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM mg_episode WHERE id=?", (episode_id,)).fetchone()
    return dict(row) if row else None


def neighbours(con: sqlite3.Connection, entity: str, hops: int = 1,
               limit: int = 40) -> list[dict[str, Any]]:
    """Bounded graph traversal over live entity-to-entity facts.

    Uses a recursive CTE with an explicit depth cap - unbounded traversal on a
    dense graph is how retrieval blows the context window.
    """
    hops = max(1, min(int(hops), 4))
    rows = con.execute(
        """
        WITH RECURSIVE reach(id, depth) AS (
            SELECT id, 0 FROM mg_entity WHERE name = ?
            UNION
            SELECT CASE WHEN f.subject_id = r.id THEN f.object_id ELSE f.subject_id END, r.depth + 1
            FROM mg_fact f
            JOIN reach r ON (f.subject_id = r.id OR f.object_id = r.id)
            WHERE f.t_invalid IS NULL AND f.object_id IS NOT NULL AND r.depth < ?
        )
        SELECT DISTINCT f.* FROM mg_fact f
        JOIN reach r ON (f.subject_id = r.id OR f.object_id = r.id)
        WHERE f.t_invalid IS NULL
        LIMIT ?
        """,
        (entity, hops, limit),
    )
    return [_row_to_fact(con, r) for r in rows]


# ------------------------------------------------------------------- retrieval


def _fts_query(query: str) -> str:
    """FTS5 MATCH string. Quote every term so user text can never be syntax."""
    terms = [t for t in re.findall(r"[A-Za-z0-9_]+", query or "") if len(t) > 1]
    return " OR ".join(f'"{t}"' for t in terms)


def search_keyword(con: sqlite3.Connection, query: str, k: int = 10) -> list[tuple[int, float]]:
    """BM25 over fact statements. Returns (fact_id, rank) best-first."""
    match = _fts_query(query)
    if not match:
        return []
    rows = con.execute(
        "SELECT rowid, bm25(mg_fact_fts) AS score FROM mg_fact_fts "
        "WHERE mg_fact_fts MATCH ? ORDER BY score LIMIT ?",
        (match, k),
    )
    # bm25() is negative-better; flip so larger is better everywhere.
    return [(int(r["rowid"]), -float(r["score"])) for r in rows]


def search_vector(con: sqlite3.Connection, embedding: Sequence[float] | None,
                  k: int = 10) -> list[tuple[int, float]]:
    """Cosine over stored fact vectors. Empty when nothing is embedded, which is
    the normal offline case - the keyword path still works."""
    if not embedding:
        return []
    scored: list[tuple[int, float]] = []
    for row in con.execute(
        "SELECT id, embedding FROM mg_fact WHERE embedding IS NOT NULL AND t_invalid IS NULL"
    ):
        score = cosine(embedding, unpack_embedding(row["embedding"]))
        if score > 0:
            scored.append((int(row["id"]), score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]


def reciprocal_rank_fusion(*rankings: list[tuple[int, float]], k: int = 60) -> list[tuple[int, float]]:
    """Fuse ranked lists by RRF. Rank-based, so incomparable score scales
    (negative BM25 vs 0-1 cosine) cannot distort the blend."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for position, (item_id, _score) in enumerate(ranking, start=1):
            fused[item_id] = fused.get(item_id, 0.0) + 1.0 / (k + position)
    return sorted(fused.items(), key=lambda pair: pair[1], reverse=True)


@dataclass
class Recall:
    """A token-bounded recall result.

    ``candidates`` is how many relevant facts were found before the budget was
    applied, so a caller can tell "nothing is known" (candidates == 0) apart from
    "too much is known to fit" (candidates > len(facts)). Conflating those two is
    how a memory system starts lying by omission.
    """

    facts: list[dict[str, Any]]
    context: str
    tokens: int
    truncated: bool
    sources: list[int]
    candidates: int = 0

    @property
    def budget_limited(self) -> bool:
        return self.candidates > len(self.facts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "facts": self.facts, "context": self.context, "tokens": self.tokens,
            "truncated": self.truncated, "sources": self.sources,
            "candidates": self.candidates, "budgetLimited": self.budget_limited,
        }


def recall(con: sqlite3.Connection, query: str, *, k: int = 8,
           embedding: Sequence[float] | None = None,
           seed_entity: str | None = None, hops: int = 1,
           max_tokens: int = 400, include_history: bool = False) -> Recall:
    """Hybrid recall: BM25 + optional vectors + bounded traversal, RRF-fused.

    Returns a **token-bounded** block. Exceeding the budget truncates by rank
    rather than returning everything, because unbounded recall is what kills a
    context window (token-budget.md applied to memory).
    """
    keyword = search_keyword(con, query, k=k * 2)
    vector = search_vector(con, embedding, k=k * 2)
    traversal: list[tuple[int, float]] = []
    if seed_entity:
        traversal = [(f["id"], 1.0) for f in neighbours(con, seed_entity, hops=hops, limit=k * 2)]

    fused = reciprocal_rank_fusion(keyword, vector, traversal)
    ids = [fact_id for fact_id, _ in fused][: k * 3]

    facts: list[dict[str, Any]] = []
    for fact_id in ids:
        row = con.execute("SELECT * FROM mg_fact WHERE id=?", (fact_id,)).fetchone()
        if not row:
            continue
        if not include_history and row["t_invalid"] is not None:
            continue  # superseded facts are history, not current truth
        facts.append(_row_to_fact(con, row))
        if len(facts) >= k:
            break

    budget_chars = max_tokens * CHARS_PER_TOKEN
    lines: list[str] = []
    used = 0
    truncated = False
    kept: list[dict[str, Any]] = []
    for fact in facts:
        line = _format_fact(fact)
        if used + len(line) + 1 > budget_chars:
            # Honesty over tidiness: if the best match alone exceeds the budget,
            # return it CLIPPED rather than reporting "nothing relevant" while
            # relevant facts exist. Silently empty recall reads as "no memory",
            # which is a different and misleading answer.
            if not kept and budget_chars > 40:
                clipped = line[: budget_chars - 4].rstrip() + " ..."
                lines.append(clipped)
                used += len(clipped)
                kept.append(fact)
            truncated = True
            break
        lines.append(line)
        used += len(line) + 1
        kept.append(fact)

    return Recall(
        facts=kept,
        context="\n".join(lines),
        tokens=math.ceil(used / CHARS_PER_TOKEN),
        truncated=truncated or len(kept) < len(facts),
        sources=sorted({f["episodeId"] for f in kept}),
        candidates=len(facts),
    )


def _format_fact(fact: dict[str, Any]) -> str:
    when = time.strftime("%Y-%m-%d", time.localtime(fact["tValid"]))
    status = "" if fact["live"] else " [superseded]"
    return f"- ({fact['kind']}, since {when}{status}) {fact['statement']} [ep:{fact['episodeId']}]"


# --------------------------------------------------------------------- hygiene


def stats(con: sqlite3.Connection) -> dict[str, Any]:
    def scalar(sql: str) -> int:
        return int(con.execute(sql).fetchone()[0])

    return {
        "episodes": scalar("SELECT COUNT(*) FROM mg_episode"),
        "entities": scalar("SELECT COUNT(*) FROM mg_entity"),
        "facts": scalar("SELECT COUNT(*) FROM mg_fact"),
        "liveFacts": scalar("SELECT COUNT(*) FROM mg_fact WHERE t_invalid IS NULL"),
        "supersededFacts": scalar("SELECT COUNT(*) FROM mg_fact WHERE t_invalid IS NOT NULL"),
        "embedded": scalar("SELECT COUNT(*) FROM mg_fact WHERE embedding IS NOT NULL"),
        "byKind": {r["kind"]: r["n"] for r in con.execute(
            "SELECT kind, COUNT(*) AS n FROM mg_fact GROUP BY kind")},
    }


def orphan_facts(con: sqlite3.Connection) -> list[int]:
    """Facts whose provenance is missing. Should always be empty; if it is not,
    that is a bug worth failing over rather than tolerating."""
    return [int(r["id"]) for r in con.execute(
        "SELECT f.id FROM mg_fact f LEFT JOIN mg_episode e ON e.id=f.episode_id "
        "WHERE e.id IS NULL"
    )]


def backfill_from_megamind(con: sqlite3.Connection, limit: int | None = None) -> dict[str, int]:
    """Seed the graph from the existing `memories` table.

    Each memory becomes an episode plus one fact, so nothing is invented and every
    fact keeps a real source. Idempotent: an episode whose text already exists is
    skipped, so running twice does not duplicate.
    """
    try:
        rows = list(con.execute(
            "SELECT id, ts, type, topic, text FROM memories ORDER BY ts ASC"
            + (f" LIMIT {int(limit)}" if limit else "")
        ))
    except sqlite3.OperationalError:
        return {"episodes": 0, "facts": 0, "skipped": 0}

    seen = {r["text"] for r in con.execute("SELECT text FROM mg_episode")}
    made_episodes = made_facts = skipped = 0
    for row in rows:
        text = (row["text"] or "").strip()
        if not text or text in seen:
            skipped += 1
            continue
        episode_id = add_episode(con, text, source="megamind",
                                 ref=f"memories:{row['id']}", ts=row["ts"])
        seen.add(text)
        made_episodes += 1
        kind = row["type"] if row["type"] in FACT_KINDS else "fact"
        topic = (row["topic"] or "").strip() or "alfred"
        try:
            assert_fact(
                con, subject=topic, predicate="recorded", obj=text[:200],
                episode_id=episode_id, kind=kind, statement=text,
                # Historical notes are observations, not a single current value:
                # they must not invalidate each other.
                single_valued=False, t_valid=row["ts"],
            )
            made_facts += 1
        except MemoryGraphError:
            continue
    return {"episodes": made_episodes, "facts": made_facts, "skipped": skipped}


# ------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memgraph",
        description="Alfred's bi-temporal memory graph: what is true now, and what changed.",
    )
    parser.add_argument("--db", default=None, help="database path (default memory/megamind.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the graph tables")
    sub.add_parser("stats", help="counts and health")

    p_assert = sub.add_parser("assert", help="assert a fact (invalidates what it contradicts)")
    p_assert.add_argument("--subject", required=True)
    p_assert.add_argument("--predicate", required=True)
    p_assert.add_argument("--object", required=True)
    p_assert.add_argument("--kind", default="fact", choices=FACT_KINDS)
    p_assert.add_argument("--statement", default=None)
    p_assert.add_argument("--source", default="cli")
    p_assert.add_argument("--episode-text", default=None,
                          help="provenance text; defaults to the statement")
    p_assert.add_argument("--multi", action="store_true",
                          help="predicate may hold several values at once")

    p_current = sub.add_parser("current", help="facts true right now")
    p_current.add_argument("--subject", default=None)
    p_current.add_argument("--predicate", default=None)
    p_current.add_argument("--kind", default=None)

    p_history = sub.add_parser("history", help="every version of a fact")
    p_history.add_argument("--subject", required=True)
    p_history.add_argument("--predicate", required=True)

    p_recall = sub.add_parser("recall", help="hybrid, token-bounded recall")
    p_recall.add_argument("-q", "--query", required=True)
    p_recall.add_argument("-k", type=int, default=8)
    p_recall.add_argument("--max-tokens", type=int, default=400)
    p_recall.add_argument("--seed-entity", default=None)
    p_recall.add_argument("--hops", type=int, default=1)
    p_recall.add_argument("--include-history", action="store_true")
    p_recall.add_argument("--no-vector", action="store_true",
                          help="skip the query embedding (keyword + traversal only)")

    sub.add_parser("backfill", help="seed the graph from the existing memories table")
    sub.add_parser("doctor", help="check provenance integrity")

    p_embed = sub.add_parser("embed", help="embed facts that have no vector yet (uses the local model)")
    p_embed.add_argument("--limit", type=int, default=None)

    args = parser.parse_args(argv)
    con = connect(args.db)
    init(con)

    if args.command == "init":
        print("memory graph ready")
    elif args.command == "stats":
        print(json.dumps(stats(con), indent=2))
    elif args.command == "assert":
        text = args.episode_text or args.statement or f"{args.subject} {args.predicate} {args.object}"
        episode_id = add_episode(con, text, source=args.source)
        outcome = assert_fact(
            con, args.subject, args.predicate, args.object, episode_id=episode_id,
            kind=args.kind, statement=args.statement, single_valued=not args.multi,
        )
        print(json.dumps(outcome.to_dict(), indent=2))
    elif args.command == "current":
        print(json.dumps(current(con, args.subject, args.predicate, args.kind), indent=2))
    elif args.command == "history":
        print(json.dumps(history(con, args.subject, args.predicate), indent=2))
    elif args.command == "recall":
        # Embed the query when the local model is up; recall still works without it.
        vector = embed(args.query) if not args.no_vector else []
        result = recall(con, args.query, k=args.k, max_tokens=args.max_tokens,
                        embedding=vector, seed_entity=args.seed_entity, hops=args.hops,
                        include_history=args.include_history)
        if result.facts:
            print(result.context)
        elif result.candidates:
            print(f"({result.candidates} relevant fact(s) found but none fit a "
                  f"{args.max_tokens}-token budget - raise --max-tokens)")
        else:
            print("(nothing relevant)")
        print(f"\n[{len(result.facts)} of {result.candidates} facts, ~{result.tokens} tokens"
              f"{', budget-limited' if result.budget_limited else ''}"
              f", vector={'on' if vector else 'off'}"
              f", sources: {result.sources}]")
    elif args.command == "embed":
        def show(done: int, total: int) -> None:
            print(f"\r  embedding {done}/{total}", end="", flush=True)
        report = backfill_embeddings(con, limit=args.limit, progress=show)
        print("\r" + json.dumps(report, indent=2))
    elif args.command == "backfill":
        print(json.dumps(backfill_from_megamind(con), indent=2))
    elif args.command == "doctor":
        orphans = orphan_facts(con)
        if orphans:
            print(f"FAIL: {len(orphans)} fact(s) without provenance: {orphans[:10]}", file=sys.stderr)
            return 1
        print("ok: every fact cites an episode")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
