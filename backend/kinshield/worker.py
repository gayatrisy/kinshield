"""Background worker: creates due reminders, escalates unanswered ones, and processes Telegram taps.

Runs as its own process (`python -m kinshield.worker`), so reminders continue with the browser closed.
State lives in SQLite, so a restart picks up where it left off.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import db, notify
from .config import settings

log = logging.getLogger("kinshield.worker")
LOOKAHEAD = timedelta(hours=24)


def _tz(conn: sqlite3.Connection) -> ZoneInfo:
    row = conn.execute("SELECT timezone FROM people WHERE role='parent' ORDER BY id LIMIT 1").fetchone()
    try:
        return ZoneInfo(row["timezone"]) if row else ZoneInfo("Asia/Kolkata")
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def plan_acknowledgements(conn: sqlite3.Connection, at: datetime | None = None) -> int:
    """Create acknowledgement rows for every scheduled time in the next 24 hours (idempotent)."""
    at = at or db.now()
    tz = _tz(conn)
    created = 0
    for s in db.rows(conn.execute("SELECT * FROM schedules WHERE active=1")):
        times = json.loads(s["times"])
        local_now = at.astimezone(tz)
        for day_offset in (0, 1):
            day = (local_now + timedelta(days=day_offset)).date()
            if day.isoformat() < s["start_date"] or (s["end_date"] and day.isoformat() > s["end_date"]):
                continue
            for t in times:
                hh, mm = (int(x) for x in t.split(":"))
                due_local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
                due = due_local.astimezone(at.tzinfo)
                # don't back-fill reminders that were already due before the schedule existed
                if due < at - timedelta(minutes=s["ack_window_minutes"]) or due > at + LOOKAHEAD:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO acknowledgements (schedule_id, due_at, window_minutes) VALUES (?,?,?)",
                    (s["id"], db.iso(due), s["ack_window_minutes"]))
                created += cur.rowcount
    return created


def _context(conn: sqlite3.Connection, ack: dict) -> dict:
    return db.one(conn.execute(
        "SELECT m.id AS med_id, m.name_as_written, m.strength, m.dose FROM acknowledgements a "
        "JOIN schedules s ON s.id=a.schedule_id JOIN medications m ON m.id=s.medication_id WHERE a.id=?",
        (ack["id"],))) or {}


def _recent_changes(conn: sqlite3.Connection) -> str:
    since = db.iso(db.now() - timedelta(days=7))
    rows = conn.execute("SELECT reason FROM care_plan_versions WHERE created_at > ? ORDER BY id DESC LIMIT 3",
                        (since,)).fetchall()
    return "; ".join(r["reason"] for r in rows) or "none in the last 7 days"


def escalation_text(conn: sqlite3.Connection, ack: dict, why: str) -> str:
    c = _context(conn, ack)
    tz = _tz(conn)
    due = db.parse_iso(ack["due_at"]).astimezone(tz).strftime("%H:%M")
    what = " ".join(x for x in [c.get("name_as_written"), c.get("strength"), c.get("dose")] if x)
    return (f"KinShield: {why}\n"
            f"Due: {what} at {due}\n"
            f"Recent plan changes: {_recent_changes(conn)}\n"
            f"Self-reported acknowledgements only; this does not show whether a dose was taken.\n"
            f"Dashboard: {settings.public_url}")


def escalate(conn: sqlite3.Connection, ws_id: str, ack: dict, why: str) -> int:
    conn.execute("UPDATE acknowledgements SET escalated_at=? WHERE id=?", (db.iso(), ack["id"]))
    nid = notify.send(conn, ws_id, why, escalation_text(conn, ack, why), acknowledgement_id=ack["id"])
    n = db.one(conn.execute("SELECT * FROM notifications WHERE id=?", (nid,)))
    db.event(conn, "escalation", f"{why} — {notify.describe(n)}", ref_table="notifications", ref_id=nid)
    return nid


def tick_workspace(conn: sqlite3.Connection, ws_id: str, at: datetime | None = None) -> dict[str, int]:
    at = at or db.now()
    stats = {"planned": 0, "reminded": 0, "escalated": 0, "resent": 0}
    stats["planned"] = plan_acknowledgements(conn, at)
    # due now: mark reminded (the parent screen shows it; a parent Telegram chat could be added here)
    for a in db.rows(conn.execute(
            "SELECT * FROM acknowledgements WHERE status='pending' AND reminded_at IS NULL AND due_at <= ?",
            (db.iso(at),))):
        conn.execute("UPDATE acknowledgements SET reminded_at=? WHERE id=?", (db.iso(at), a["id"]))
        stats["reminded"] += 1
    # no response within the window
    for a in db.rows(conn.execute("SELECT * FROM acknowledgements WHERE status='pending'")):
        if db.parse_iso(a["due_at"]) + timedelta(minutes=a["window_minutes"]) <= at:
            conn.execute("UPDATE acknowledgements SET status='no_response' WHERE id=?", (a["id"],))
            escalate(conn, ws_id, a, "No response to a medicine reminder")
            stats["escalated"] += 1
    # accepted alerts nobody tapped 'Seen' on: resend once after one more window
    window = settings.default_ack_window_minutes
    for n in db.rows(conn.execute(
            "SELECT * FROM notifications WHERE api_status='accepted' AND seen_at IS NULL AND resend_of IS NULL "
            "AND id NOT IN (SELECT resend_of FROM notifications WHERE resend_of IS NOT NULL)")):
        if db.parse_iso(n["sent_at"]) + timedelta(minutes=window) <= at:
            nid = notify.send(conn, ws_id, n["reason"] + " (reminder)",
                              "Not yet marked as seen.\n\n" + n["body"],
                              acknowledgement_id=n["acknowledgement_id"], resend_of=n["id"])
            db.event(conn, "escalation_resent", f"Alert #{n['id']} re-sent once: not yet seen",
                     ref_table="notifications", ref_id=nid)
            stats["resent"] += 1
    return stats


def tick_all() -> None:
    for ws_id in db.live_workspaces():
        conn = db.open_workspace(ws_id)
        try:
            tick_workspace(conn, ws_id)
        except Exception:
            log.exception("worker tick failed for workspace %s", ws_id)
        finally:
            conn.close()
    notify.poll_updates(db.open_workspace)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    log.info("worker started; interval %ss; demo mode %s", settings.worker_interval_s, settings.demo_mode)
    last_purge = 0.0
    while True:
        started = time.monotonic()
        try:
            tick_all()
            if time.monotonic() - last_purge > 3600:
                n = db.purge_expired()
                if n:
                    log.info("purged %d expired workspaces", n)
                last_purge = time.monotonic()
        except Exception:
            log.exception("worker loop error")
        time.sleep(max(1.0, settings.worker_interval_s - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
