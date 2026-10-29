"""Label-evidence review: openFDA label text searched in code; Nemotron only explains passages that matched."""
from __future__ import annotations

import sqlite3
from typing import Any

from . import db, drugs, llm
from .config import settings

OUTCOME_TEXT = {
    "mention_found": "Label mentions {other}: discuss with your clinician",
    "no_mention": "Label found, no mention of your other medications",
    "no_label": "No label information available",
    "lookup_failed": "Label lookup failed; try again later",
}
NOT_SAFETY = "A label check is not a safety check."

EXPLAIN_SYSTEM = """You help a family caregiver understand a passage from an official US drug label (openFDA).
You are given the medicine the passage comes from, another medicine the patient takes, and the passage.
Explain in at most two plain sentences what the passage says about the two medicines together.
Then write one question the caregiver can ask the doctor or pharmacist.
Only use the passage. Do not say whether the combination is safe or unsafe, and do not recommend any change.
Return JSON: {"explanation": "...", "question": "..."}"""


def _ingredient(m: dict[str, Any]) -> str:
    return (m.get("ingredient") or drugs.clean_name(m["name_as_written"]) or "").strip()


def review(conn: sqlite3.Connection, med_id: int) -> list[int]:
    """Check one medication's label against every other active medication. Returns evidence ids."""
    med = db.one(conn.execute("SELECT * FROM medications WHERE id=?", (med_id,)))
    if not med or med["status"] != "active":
        return []
    others = [m for m in db.rows(conn.execute(
        "SELECT * FROM medications WHERE status='active' AND id != ?", (med_id,)))]
    conn.execute("DELETE FROM evidence WHERE medication_id=?", (med_id,))
    ing = _ingredient(med)
    ids: list[int] = []

    def add(outcome: str, other: dict[str, Any] | None, **kw: Any) -> None:
        cur = conn.execute(
            "INSERT INTO evidence (medication_id, other_medication_id, source, outcome, set_id, section, passage, "
            "explanation, clinician_question, retrieved_at, url) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (med_id, other["id"] if other else None, "openFDA", outcome, kw.get("set_id"), kw.get("section"),
             kw.get("passage"), kw.get("explanation"), kw.get("question"), db.iso(), kw.get("url")))
        ids.append(int(cur.lastrowid))

    try:
        label = drugs.fetch_label(ing) if ing else None
    except drugs.LookupFailed as exc:
        add("lookup_failed", None, passage=str(exc)[:300])
        return ids
    if not label:
        add("no_label", None)
        db.event(conn, "label_review", f"{med['name_as_written']}: no label information available",
                 ref_table="medications", ref_id=med_id)
        return ids
    found_any = False
    for other in others:
        o_ing = _ingredient(other)
        if not o_ing or o_ing == ing:
            continue
        for section, passage in drugs.find_mentions(label, o_ing)[:1]:
            found_any = True
            explanation = question = None
            try:
                data, _ = llm.chat_json(
                    conn, "label_evidence", settings.model_deep, EXPLAIN_SYSTEM,
                    f"Medicine with this label: {ing}\nOther medicine the patient takes: {o_ing}\n"
                    f"Label section: {section}\nPassage: {passage}", max_tokens=6000)
                explanation = str(data.get("explanation") or "")[:600] or None
                question = str(data.get("question") or "")[:300] or None
            except (llm.LLMError, ValueError, AttributeError):
                pass  # the quoted passage still stands on its own
            add("mention_found", other, set_id=label.set_id, section=section, passage=passage,
                explanation=explanation, question=question, url=label.url)
    if not found_any:
        add("no_mention", None, set_id=label.set_id, url=label.url)
    db.event(conn, "label_review", f"{med['name_as_written']}: label checked against {len(others)} other "
             f"medicines ({'mentions found' if found_any else 'no mentions found'})",
             ref_table="medications", ref_id=med_id)
    return ids
