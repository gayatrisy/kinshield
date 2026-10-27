"""The assistant's memory view: confirmed records with stable ids, used for answers and the visit brief.

Model output must cite these ids; code drops any sentence whose citations don't exist.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import timedelta
from typing import Any

from . import db, llm
from .redact import Redactor
from .config import settings


def records(conn: sqlite3.Connection, days: int = 30) -> dict[str, dict[str, Any]]:
    since = db.iso(db.now() - timedelta(days=days))
    out: dict[str, dict[str, Any]] = {}
    red = Redactor([r["name"] for r in conn.execute("SELECT name FROM people").fetchall()]).redact
    for m in db.rows(conn.execute("SELECT * FROM medications ORDER BY id")):
        s = db.one(conn.execute("SELECT times FROM schedules WHERE medication_id=? AND active=1", (m["id"],)))
        out[f"med:{m['id']}"] = {
            "type": "medication", "status": m["status"], "name": m["name_as_written"], "ingredient": m["ingredient"],
            "strength": m["strength"], "dose": m["dose"], "frequency": m["frequency"],
            "instructions": red(m["instructions"] or ""), "reminder_times": json.loads(s["times"]) if s else [],
            "ask_doctor_whether_to_continue": bool(m["ask_doctor"]), "updated": m["updated_at"][:10]}
    for e in db.rows(conn.execute(
            "SELECT * FROM timeline_events WHERE at > ? AND kind IN ('allergy','follow_up','instruction','symptom',"
            "'plan_decision','escalation','alert_seen','label_review','note') ORDER BY id", (since,))):
        out[f"event:{e['id']}"] = {"type": e["kind"], "at": e["at"][:16], "summary": red(e["summary"])}
    # allergies are kept regardless of age
    for e in db.rows(conn.execute("SELECT * FROM timeline_events WHERE kind='allergy' ORDER BY id")):
        out[f"event:{e['id']}"] = {"type": "allergy", "at": e["at"][:16], "summary": e["summary"]}
    for ev in db.rows(conn.execute(
            "SELECT e.*, m.name_as_written AS med, o.name_as_written AS other FROM evidence e "
            "JOIN medications m ON m.id=e.medication_id LEFT JOIN medications o ON o.id=e.other_medication_id "
            "WHERE m.status='active'")):
        out[f"evidence:{ev['id']}"] = {
            "type": "label_evidence", "outcome": ev["outcome"], "medicine": ev["med"], "other_medicine": ev["other"],
            "label_passage": ev["passage"], "clinician_question": ev["clinician_question"]}
    for row in ack_summary(conn, days):
        out[f"adherence:{row['medication_id']}"] = {
            "type": "self_reported_acknowledgements", "medicine": row["name"], "days": days,
            "acknowledged": row["acknowledged"], "skipped": row["skipped"], "help": row["help"],
            "no_response": row["no_response"]}
    return out


def ack_summary(conn: sqlite3.Connection, days: int = 30) -> list[dict[str, Any]]:
    since = db.iso(db.now() - timedelta(days=days))
    return db.rows(conn.execute(
        "SELECT m.id AS medication_id, m.name_as_written AS name, "
        "SUM(a.status='acknowledged') AS acknowledged, SUM(a.status='skipped') AS skipped, "
        "SUM(a.status='help') AS help, SUM(a.status='no_response') AS no_response "
        "FROM acknowledgements a JOIN schedules s ON s.id=a.schedule_id JOIN medications m ON m.id=s.medication_id "
        "WHERE a.due_at > ? AND a.due_at <= ? AND a.status != 'cancelled' GROUP BY m.id ORDER BY m.id",
        (since, db.iso())))


def check_cites(items: list[dict[str, Any]], valid: set[str]) -> list[dict[str, Any]]:
    """Keep only statements whose every citation exists; normalise the cite list."""
    kept = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "").strip()
        cites = [str(c).strip() for c in (it.get("cites") or []) if str(c).strip()]
        if text and cites and all(c in valid for c in cites):
            kept.append({"text": text, "cites": cites})
    return kept


ASK_SYSTEM = """You are KinShield, a private assistant helping a family caregiver keep track of an older
parent's care. Answer the caregiver's question using ONLY the records provided; each record has an id such as
med:3 or event:12. Refer to the parent as "your parent".

Rules:
- Every sentence must cite at least one record id it relies on.
- If the records do not answer the question, say so in one sentence citing nothing, and suggest what to record.
- Acknowledgements are self-reported; never say a dose was or wasn't taken.
- Never recommend starting, stopping, or changing a medicine, and never judge whether something is safe.
  For medical decisions, suggest asking the doctor or pharmacist.

Return JSON: {"answer": [{"text": "...", "cites": ["med:3"]}], "unanswerable": false}"""


def ask(conn: sqlite3.Connection, question: str) -> dict[str, Any]:
    recs = records(conn)
    payload = json.dumps(recs, ensure_ascii=False)
    data, run_id = llm.chat_json(conn, "ask", settings.model_main, ASK_SYSTEM,
                                 f"Records:\n{payload}\n\nCaregiver's question: {question[:500]}", max_tokens=2000)
    answer = check_cites(data.get("answer", []) if isinstance(data, dict) else [], set(recs))
    dropped = len(data.get("answer", []) if isinstance(data, dict) else []) - len(answer)
    if not answer:
        answer = [{"text": "I couldn't find that in the confirmed records yet.", "cites": []}]
    db.event(conn, "ask", f"Caregiver asked: {question[:120]}", ref_table="model_runs", ref_id=run_id)
    return {"answer": answer, "dropped_uncited": max(0, dropped), "model_run_id": run_id,
            "records": {c: recs[c] for it in answer for c in it["cites"]}}


def add_symptom(conn: sqlite3.Connection, text: str) -> int:
    text = re.sub(r"\s+", " ", text).strip()[:500]
    return db.event(conn, "symptom", f"Reported: {text}", person_id=db.parent_id(conn))
