"""SQLite storage: one registry database plus one database per workspace."""
from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    db_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

WORKSPACE_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('caregiver','parent')),
    name TEXT NOT NULL,
    telegram_chat_id TEXT,
    telegram_link_code TEXT,
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata'
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    file_path TEXT NOT NULL,
    original_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    has_text_layer INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'uploaded'
);
CREATE TABLE IF NOT EXISTS ocr_lines (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    line_no INTEGER NOT NULL,
    text TEXT NOT NULL,
    bbox TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('text_layer','ocr','vision')),
    redacted_text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extractions (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    model_run_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','rejected','failed')),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extracted_items (
    id INTEGER PRIMARY KEY,
    extraction_id INTEGER NOT NULL REFERENCES extractions(id) ON DELETE CASCADE,
    group_kind TEXT NOT NULL,
    group_index INTEGER NOT NULL,
    field TEXT NOT NULL,
    value TEXT,
    source_line_ids TEXT NOT NULL DEFAULT '[]',
    uncertain INTEGER NOT NULL DEFAULT 0,
    uncertain_reason TEXT,
    requires_confirm INTEGER NOT NULL DEFAULT 0,
    suggestion_json TEXT,
    caregiver_value TEXT,
    confirmed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS confirmations (
    id INTEGER PRIMARY KEY,
    actor_person_id INTEGER NOT NULL REFERENCES people(id),
    target_table TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS medications (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    name_as_written TEXT NOT NULL,
    rxcui TEXT,
    ingredient TEXT,
    ingredient_source TEXT,
    strength TEXT,
    form TEXT,
    dose TEXT,
    frequency TEXT,
    instructions TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','stopped')),
    source_item_id INTEGER,
    confirmation_id INTEGER,
    ask_doctor INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS care_plan_versions (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    created_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    confirmation_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY,
    medication_id INTEGER NOT NULL REFERENCES medications(id),
    times TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT,
    ack_window_minutes INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS acknowledgements (
    id INTEGER PRIMARY KEY,
    schedule_id INTEGER NOT NULL REFERENCES schedules(id),
    due_at TEXT NOT NULL,
    window_minutes INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','acknowledged','skipped','help','no_response','cancelled')),
    reminded_at TEXT,
    responded_at TEXT,
    escalated_at TEXT,
    UNIQUE (schedule_id, due_at)
);
CREATE TABLE IF NOT EXISTS reconciliation_findings (
    id INTEGER PRIMARY KEY,
    extraction_id INTEGER NOT NULL REFERENCES extractions(id),
    medication_id INTEGER REFERENCES medications(id),
    group_index INTEGER,
    kind TEXT NOT NULL CHECK (kind IN ('new','changed','explicitly_stopped','possible_duplicate','not_mentioned','unchanged')),
    source_line_ids TEXT NOT NULL DEFAULT '[]',
    detail_json TEXT NOT NULL,
    note TEXT,
    decision TEXT,
    confirmation_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY,
    medication_id INTEGER NOT NULL REFERENCES medications(id),
    other_medication_id INTEGER REFERENCES medications(id),
    source TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('mention_found','no_mention','no_label','lookup_failed')),
    set_id TEXT,
    section TEXT,
    passage TEXT,
    explanation TEXT,
    clinician_question TEXT,
    retrieved_at TEXT NOT NULL,
    url TEXT
);
CREATE TABLE IF NOT EXISTS timeline_events (
    id INTEGER PRIMARY KEY,
    person_id INTEGER,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    ref_table TEXT,
    ref_id INTEGER
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    person_id INTEGER,
    channel TEXT NOT NULL,
    reason TEXT NOT NULL,
    body TEXT NOT NULL,
    acknowledgement_id INTEGER,
    api_status TEXT NOT NULL CHECK (api_status IN ('accepted','failed','not_configured')),
    telegram_message_id TEXT,
    error TEXT,
    sent_at TEXT NOT NULL,
    seen_at TEXT,
    resend_of INTEGER
);
CREATE TABLE IF NOT EXISTS model_runs (
    id INTEGER PRIMARY KEY,
    task TEXT NOT NULL,
    model_id TEXT NOT NULL,
    redacted_prompt TEXT NOT NULL,
    response_text TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_json TEXT NOT NULL
);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def hash_token(token: str) -> str:
    return hashlib.sha256((settings.secret_key + ":" + token).encode()).hexdigest()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def registry() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = _connect(settings.data_dir / "registry.db")
    conn.executescript(REGISTRY_SCHEMA)
    return conn


def workspace_dir(ws_id: str) -> Path:
    return settings.data_dir / "ws" / ws_id


def open_workspace(ws_id: str) -> sqlite3.Connection:
    d = workspace_dir(ws_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "uploads").mkdir(exist_ok=True)
    conn = _connect(d / "kinshield.db")
    conn.executescript(WORKSPACE_SCHEMA)
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def create_workspace(is_demo: bool = True) -> tuple[str, str]:
    """Create a workspace; returns (workspace_id, raw_token). Only the hash of the token is stored."""
    ws_id = secrets.token_hex(8)
    token = secrets.token_urlsafe(32)
    reg = registry()
    t = now()
    reg.execute(
        "INSERT INTO workspaces (id, token_hash, db_path, created_at, last_seen_at, expires_at, is_demo) "
        "VALUES (?,?,?,?,?,?,?)",
        (ws_id, hash_token(token), str(workspace_dir(ws_id) / "kinshield.db"), iso(t), iso(t),
         iso(t + timedelta(days=settings.workspace_ttl_days)), int(is_demo)),
    )
    reg.close()
    open_workspace(ws_id).close()
    return ws_id, token


def resolve_token(token: str) -> str | None:
    if not token:
        return None
    reg = registry()
    row = reg.execute("SELECT id, expires_at FROM workspaces WHERE token_hash=?", (hash_token(token),)).fetchone()
    if not row or parse_iso(row["expires_at"]) < now():
        reg.close()
        return None
    t = now()
    reg.execute(
        "UPDATE workspaces SET last_seen_at=?, expires_at=? WHERE id=?",
        (iso(t), iso(t + timedelta(days=settings.workspace_ttl_days)), row["id"]),
    )
    reg.close()
    return row["id"]


def live_workspaces() -> list[str]:
    reg = registry()
    rows = reg.execute("SELECT id FROM workspaces WHERE expires_at > ?", (iso(),)).fetchall()
    reg.close()
    return [r["id"] for r in rows]


def purge_expired() -> int:
    reg = registry()
    rows = reg.execute("SELECT id FROM workspaces WHERE expires_at <= ?", (iso(),)).fetchall()
    for r in rows:
        shutil.rmtree(workspace_dir(r["id"]), ignore_errors=True)
        reg.execute("DELETE FROM workspaces WHERE id=?", (r["id"],))
    reg.close()
    return len(rows)


def delete_workspace(ws_id: str) -> None:
    reg = registry()
    reg.execute("DELETE FROM workspaces WHERE id=?", (ws_id,))
    reg.close()
    shutil.rmtree(workspace_dir(ws_id), ignore_errors=True)


def kv_get(key: str) -> str | None:
    reg = registry()
    row = reg.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    reg.close()
    return row["value"] if row else None


def kv_set(key: str, value: str) -> None:
    reg = registry()
    reg.execute("INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
    reg.close()


def rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def one(cur: sqlite3.Cursor) -> dict[str, Any] | None:
    r = cur.fetchone()
    return dict(r) if r else None


def event(conn: sqlite3.Connection, kind: str, summary: str, *, person_id: int | None = None,
          ref_table: str | None = None, ref_id: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO timeline_events (person_id, at, kind, summary, ref_table, ref_id) VALUES (?,?,?,?,?,?)",
        (person_id, iso(), kind, summary, ref_table, ref_id),
    )
    return int(cur.lastrowid)


def confirm(conn: sqlite3.Connection, actor_id: int, table: str, target_id: int, action: str,
            before: Any = None, after: Any = None) -> int:
    cur = conn.execute(
        "INSERT INTO confirmations (actor_person_id, target_table, target_id, action, before_json, after_json, at) "
        "VALUES (?,?,?,?,?,?,?)",
        (actor_id, table, target_id, action,
         json.dumps(before) if before is not None else None,
         json.dumps(after) if after is not None else None, iso()),
    )
    return int(cur.lastrowid)


def caregiver_id(conn: sqlite3.Connection) -> int:
    r = conn.execute("SELECT id FROM people WHERE role='caregiver' ORDER BY id LIMIT 1").fetchone()
    if not r:
        raise LookupError("No caregiver in this workspace")
    return int(r["id"])


def parent_id(conn: sqlite3.Connection) -> int:
    r = conn.execute("SELECT id FROM people WHERE role='parent' ORDER BY id LIMIT 1").fetchone()
    if not r:
        raise LookupError("No parent in this workspace")
    return int(r["id"])
