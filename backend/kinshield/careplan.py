"""Confirmed medication list, schedules and care-plan versions."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from typing import Any

from . import db
from .config import settings

SLOT = {"morning": "08:00", "afternoon": "14:00", "evening": "18:00", "night": "21:00"}


def times_from_frequency(frequency: str | None, timing: str | None = None) -> list[str]:
    """Suggest reminder times from common prescription notation. The caregiver confirms or edits them."""
    f = f"{frequency or ''} {timing or ''}".lower()
    m = re.search(r"\b([0-9½]+)\s*-\s*([0-9½]+)\s*-\s*([0-9½]+)\b", f)
    if m:  # Indian 1-0-1 notation: morning-afternoon-night
        slots = []
        for val, slot in zip(m.groups(), ("morning", "afternoon", "night")):
            if val not in ("0",):
                slots.append(SLOT[slot])
        return slots or [SLOT["morning"]]
    if re.search(r"\b(sos|prn|as needed|when required)\b", f):
        return []
    if re.search(r"\b(tds|tid|thrice|three times|3 times)\b", f):
        return ["08:00", "14:00", "21:00"]
    if re.search(r"\b(bd|bid|twice|two times|2 times|every 12)\b", f):
        return ["08:00", "21:00"]
    if re.search(r"\b(qid|four times|4 times)\b", f):
        return ["08:00", "12:00", "16:00", "21:00"]
    if re.search(r"\b(hs|bedtime|at night|night)\b", f):
        return [SLOT["night"]]
    if re.search(r"\b(evening)\b", f):
        return [SLOT["evening"]]
    if re.search(r"\b(od|once|daily|morning|every day|qd)\b", f):
        return [SLOT["morning"]]
    return [SLOT["morning"]]


def valid_times(times: list[str]) -> list[str]:
    out = []
    for t in times:
        if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t.strip()):
            out.append(t.strip())
    return sorted(set(out))


def active_meds(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return db.rows(conn.execute("SELECT * FROM medications WHERE status='active' ORDER BY id"))


def snapshot(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    meds = active_meds(conn)
    for m in meds:
        s = db.one(conn.execute("SELECT times FROM schedules WHERE medication_id=? AND active=1", (m["id"],)))
        m["times"] = json.loads(s["times"]) if s else []
    return [{k: m[k] for k in ("id", "name_as_written", "ingredient", "strength", "dose", "frequency",
                                "instructions", "times")} for m in meds]


def new_version(conn: sqlite3.Connection, reason: str, confirmation_ids: list[int]) -> int:
    person = db.parent_id(conn)
    cur = conn.execute(
        "INSERT INTO care_plan_versions (person_id, created_at, reason, snapshot_json, confirmation_ids) "
        "VALUES (?,?,?,?,?)",
        (person, db.iso(), reason, json.dumps(snapshot(conn)), json.dumps(confirmation_ids)))
    return int(cur.lastrowid)


def set_schedule(conn: sqlite3.Connection, med_id: int, times: list[str], window: int | None = None) -> int | None:
    conn.execute("UPDATE schedules SET active=0 WHERE medication_id=? AND active=1", (med_id,))
    # cancel future pending reminders of the old schedule
    conn.execute(
        "UPDATE acknowledgements SET status='cancelled' WHERE status='pending' AND due_at > ? AND schedule_id IN "
        "(SELECT id FROM schedules WHERE medication_id=? AND active=0)", (db.iso(), med_id))
    times = valid_times(times)
    if not times:
        return None
    cur = conn.execute(
        "INSERT INTO schedules (medication_id, times, start_date, ack_window_minutes, active) VALUES (?,?,?,?,1)",
        (med_id, json.dumps(times), date.today().isoformat(), window or settings.default_ack_window_minutes))
    return int(cur.lastrowid)


def add_medication(conn: sqlite3.Connection, fields: dict[str, Any], *, source_item_id: int | None,
                   confirmation_id: int | None, times: list[str]) -> int:
    t = db.iso()
    cur = conn.execute(
        "INSERT INTO medications (person_id, name_as_written, rxcui, ingredient, ingredient_source, strength, form, "
        "dose, frequency, instructions, status, source_item_id, confirmation_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?, 'active', ?,?,?,?)",
        (db.parent_id(conn), fields.get("name"), fields.get("rxcui"), fields.get("ingredient"),
         fields.get("ingredient_source"), fields.get("strength"), fields.get("form"), fields.get("dose"),
         fields.get("frequency"), fields.get("instructions"), source_item_id, confirmation_id, t, t))
    med_id = int(cur.lastrowid)
    set_schedule(conn, med_id, times)
    return med_id


def stop_medication(conn: sqlite3.Connection, med_id: int) -> None:
    conn.execute("UPDATE medications SET status='stopped', updated_at=? WHERE id=?", (db.iso(), med_id))
    conn.execute("UPDATE schedules SET active=0 WHERE medication_id=?", (med_id,))
    conn.execute("UPDATE acknowledgements SET status='cancelled' WHERE status='pending' AND schedule_id IN "
                 "(SELECT id FROM schedules WHERE medication_id=?)", (med_id,))
