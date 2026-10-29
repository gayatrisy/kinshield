"""Visit brief: facts come straight from the database; the model writes only cited summary lines and questions."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from xml.sax.saxutils import escape as esc

from . import db, llm, memory
from .config import settings

BRIEF_SYSTEM = """You prepare a short pre-visit brief that a family caregiver will hand to a doctor.
Use ONLY the records provided; each has an id such as med:3 or event:12. Refer to the patient as "the patient".

Write:
- "summary": 3 to 6 sentences covering medication changes, reported symptoms, and the self-reported
  acknowledgement pattern. Every sentence cites the record ids it uses.
- "questions": 3 to 6 questions for the clinician, each citing the records that prompted it. Include any
  medicine marked ask_doctor_whether_to_continue and any label_evidence with outcome mention_found.

Never diagnose, never say a combination is safe or unsafe, never recommend changes, and never claim a dose was
taken or missed (acknowledgements are self-reported).
Return JSON: {"summary": [{"text": "...", "cites": ["med:3"]}], "questions": [{"text": "...", "cites": ["event:9"]}]}"""


def build(conn: sqlite3.Connection, ws_dir: Path, days: int = 30) -> dict[str, Any]:
    recs = memory.records(conn, days)
    data, run_id = llm.chat_json(conn, "visit_brief", settings.model_main, BRIEF_SYSTEM,
                                 "Records:\n" + json.dumps(recs, ensure_ascii=False), max_tokens=8000)
    data = data if isinstance(data, dict) else {}
    valid = set(recs)
    summary = memory.check_cites(data.get("summary", []), valid)
    questions = memory.check_cites(data.get("questions", []), valid)
    dropped = (len(data.get("summary", []) or []) + len(data.get("questions", []) or [])) - len(summary) - len(questions)
    # questions that must appear even if the model left them out
    for key, r in recs.items():
        if r["type"] == "medication" and r.get("ask_doctor_whether_to_continue") and r["status"] == "active" \
                and not any(key in q["cites"] for q in questions):
            questions.append({"text": f"The latest discharge document does not mention {r['name']}. "
                                      f"Should the patient continue it?", "cites": [key]})
        if r["type"] == "label_evidence" and r["outcome"] == "mention_found" \
                and not any(key in q["cites"] for q in questions):
            questions.append({"text": r.get("clinician_question") or
                              f"The {r['medicine']} label mentions {r['other_medicine']}. Is this relevant for the patient?",
                              "cites": [key]})
    content = {"summary": summary, "questions": questions, "dropped_uncited": max(0, dropped),
               "records": recs, "model_run_id": run_id, "days": days, "created_at": db.iso()}
    cur = conn.execute("INSERT INTO briefs (created_at, file_path, content_json) VALUES (?,?,?)",
                       (db.iso(), "", json.dumps(content)))
    bid = int(cur.lastrowid)
    path = ws_dir / "uploads" / f"visit-brief-{bid}.pdf"
    render_pdf(conn, content, path)
    conn.execute("UPDATE briefs SET file_path=? WHERE id=?", (str(path), bid))
    db.event(conn, "visit_brief", f"Visit brief #{bid} prepared ({len(questions)} questions)",
             ref_table="briefs", ref_id=bid)
    content["id"] = bid
    return content


def _cite(cites: list[str]) -> str:
    return " ".join(f"<font color='#1f5f8b' size='7'>[{c}]</font>" for c in cites)


def render_pdf(conn: sqlite3.Connection, content: dict[str, Any], path: Path) -> None:
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9.5, leading=13)
    small = ParagraphStyle("s", parent=body, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
    h = ParagraphStyle("h", parent=ss["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
    story: list[Any] = [
        Paragraph("Pre-visit brief", ss["Title"]),
        Paragraph(f"Prepared {content['created_at'][:10]} by KinShield from caregiver-confirmed records "
                  f"(last {content['days']} days). Each statement cites its record; see the record list at the end.",
                  small),
        Paragraph("Acknowledgements are self-reported by the patient and do not show whether doses were taken. "
                  "Label passages are from openFDA and are not a safety assessment.", small),
    ]
    meds = [r for k, r in content["records"].items() if r["type"] == "medication" and r["status"] == "active"]
    story.append(Paragraph("Current medicines", h))
    rows = [["Medicine", "Strength", "Dose", "Frequency", "Reminders"]]
    for m in meds:
        rows.append([Paragraph(esc(m["name"] or ""), body), m["strength"] or "", Paragraph(esc(m["dose"] or ""), body),
                     Paragraph(esc(m["frequency"] or ""), body), ", ".join(m["reminder_times"])])
    t = Table(rows, colWidths=[55 * mm, 22 * mm, 30 * mm, 35 * mm, 30 * mm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f5")),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d0d6")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(t)

    adh = [r for r in content["records"].values() if r["type"] == "self_reported_acknowledgements"]
    if adh:
        story.append(Paragraph("Self-reported acknowledgements", h))
        rows = [["Medicine", "Acknowledged", "Skipped", "Asked for help", "No response"]]
        rows += [[Paragraph(esc(a["medicine"] or ""), body), a["acknowledged"], a["skipped"], a["help"], a["no_response"]] for a in adh]
        t = Table(rows, colWidths=[62 * mm, 27 * mm, 22 * mm, 28 * mm, 27 * mm])
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f5")),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d0d6"))]))
        story.append(t)

    story.append(Paragraph("Summary", h))
    for s in content["summary"] or [{"text": "No summary could be produced from cited records.", "cites": []}]:
        story.append(Paragraph(f"{esc(s['text'])} {_cite(s['cites'])}", body))
    story.append(Paragraph("Questions for the clinician", h))
    for i, q in enumerate(content["questions"], 1):
        story.append(Paragraph(f"{i}. {esc(q['text'])} {_cite(q['cites'])}", body))

    ev = [(k, r) for k, r in content["records"].items() if r["type"] == "label_evidence" and r["outcome"] == "mention_found"]
    if ev:
        story.append(Paragraph("Label passages (openFDA)", h))
        for k, r in ev:
            story.append(Paragraph(f"<b>{esc(r['medicine'] or '')}</b> label mentions <b>{esc(r['other_medicine'] or '')}</b>: "
                                   f"<i>“{esc(r['label_passage'] or '')}”</i> {_cite([k])}", body))

    story.append(Paragraph("Records cited", h))
    cited = sorted({c for part in (content["summary"], content["questions"]) for it in part for c in it["cites"]})
    for c in cited:
        r = content["records"][c]
        desc = r.get("summary") or r.get("name") or r.get("medicine") or r["type"]
        story.append(Paragraph(f"[{c}] {r['type']}: {esc(str(desc))}", small))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Generated with NVIDIA Nemotron via Nebius Token Factory. Not medical advice.", small))
    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                      topMargin=14 * mm, bottomMargin=14 * mm, title="Pre-visit brief").build(story)


def latest(conn: sqlite3.Connection) -> dict[str, Any] | None:
    r = db.one(conn.execute("SELECT * FROM briefs ORDER BY id DESC LIMIT 1"))
    if not r:
        return None
    c = json.loads(r["content_json"])
    c["id"] = r["id"]
    return c

