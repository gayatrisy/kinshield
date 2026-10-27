"""Document intake: local read → redaction → Token Factory extraction → validation against source lines."""
from __future__ import annotations

import base64
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import db, drugs, llm, ocr
from .config import settings
from .redact import Redactor

MED_FIELDS = ["name", "strength", "form", "dose", "frequency", "timing", "duration", "instructions", "action"]
ALWAYS_CONFIRM = {"name", "strength", "dose"}
ACTIONS = {"start", "continue", "change", "stop", "unclear"}

EXTRACT_SYSTEM = """You read hospital discharge summaries and prescriptions and turn them into structured data.
Identifiers were replaced with placeholders like [PATIENT] or [ID]; never try to guess them.
Each input line starts with its id, e.g. "L12: Tab Metoprolol 25 mg 1-0-1".

Return ONLY a JSON object with this shape:
{
  "medications": [
    {"name": "drug name exactly as written", "strength": "e.g. 25 mg or null", "form": "tablet/capsule/... or null",
     "dose": "amount per intake as written, e.g. 1 tablet, or null",
     "frequency": "as written, e.g. 1-0-1, twice daily, OD, or null",
     "timing": "e.g. after food, at bedtime, or null", "duration": "e.g. 30 days, or null",
     "instructions": "any other instruction for this drug, or null",
     "action": "start | continue | change | stop | unclear",
     "source_lines": ["L12"]}
  ],
  "allergies": [{"substance": "...", "reaction": "... or null", "source_lines": ["L4"]}],
  "follow_ups": [{"what": "...", "when": "as written or null", "source_lines": ["L30"]}],
  "other_instructions": [{"text": "...", "source_lines": ["L33"]}]
}

Rules:
- Copy values as written in the document. Do not convert, correct, or fill in anything that is not written.
- Use null when a value is not stated. Do not infer a strength or dose.
- action is "stop" only if the document explicitly says to stop, discontinue, or withhold the drug.
- action is "unclear" if you cannot tell from the text whether to start, continue, change or stop.
- source_lines must list every line id that the values came from.
- Do not give medical advice and do not add drugs that are not in the text."""


def _person_names(conn: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in conn.execute("SELECT name FROM people").fetchall()]


def ingest(conn: sqlite3.Connection, ws_dir: Path, doc_id: int) -> None:
    """Read a stored upload and save its lines (raw and redacted). Runs locally only."""
    doc = db.one(conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)))
    if not doc:
        raise LookupError("document not found")
    result = ocr.read_document(Path(doc["file_path"]), ws_dir / "uploads" / f"doc-{doc_id}")
    redactor = Redactor(_person_names(conn))
    with db.tx(conn):
        conn.execute("DELETE FROM ocr_lines WHERE document_id=?", (doc_id,))
        for ln in result.lines:
            conn.execute(
                "INSERT INTO ocr_lines (document_id, page, line_no, text, bbox, source, redacted_text) "
                "VALUES (?,?,?,?,?,?,?)",
                (doc_id, ln.page, ln.line_no, ln.text, json.dumps([round(v, 4) for v in ln.bbox]), ln.source,
                 redactor.redact(ln.text)),
            )
        conn.execute("UPDATE documents SET page_count=?, has_text_layer=?, status='read' WHERE id=?",
                     (len(result.page_images), int(result.has_text_layer), doc_id))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _value_in_lines(value: str, texts: list[str]) -> bool:
    nv = _norm(value)
    if not nv:
        return True
    joined = _norm(" ".join(texts))
    if nv in joined:
        return True
    # allow small OCR/formatting differences: every token of the value must appear
    tokens = [_norm(t) for t in re.split(r"[\s,/]+", value) if _norm(t)]
    return bool(tokens) and all(t in joined for t in tokens)


def extract(conn: sqlite3.Connection, doc_id: int) -> int:
    """Send redacted lines to Token Factory, validate the reply, and store reviewable items. Returns extraction id."""
    lines = db.rows(conn.execute(
        "SELECT id, page, line_no, redacted_text FROM ocr_lines WHERE document_id=? ORDER BY page, line_no",
        (doc_id,)))
    if not lines:
        raise ValueError("No readable text on this document. Try a clearer photo or use the vision fallback.")
    by_label = {f"L{l['id']}": l for l in lines}
    body = "\n".join(f"L{l['id']}: {l['redacted_text']}" for l in lines)
    model = settings.model_fast if len(body) < 6000 else settings.model_main
    data, run_id = llm.chat_json(conn, "extract_document", model, EXTRACT_SYSTEM,
                                 "Document text (page order):\n" + body, max_tokens=6000)
    return _store_extraction(conn, doc_id, data, run_id, by_label)


def _store_extraction(conn: sqlite3.Connection, doc_id: int, data: dict[str, Any], run_id: int,
                      by_label: dict[str, dict[str, Any]]) -> int:
    with db.tx(conn):
        cur = conn.execute(
            "INSERT INTO extractions (document_id, model_run_id, status, payload_json, created_at) VALUES (?,?,?,?,?)",
            (doc_id, run_id, "pending", json.dumps(data), db.iso()))
        ext_id = int(cur.lastrowid)

        def add(kind: str, idx: int, fld: str, value: Any, src: list[str], *, requires: bool = False,
                suggestion: dict[str, Any] | None = None) -> None:
            value = None if value in (None, "", "null") else str(value).strip()
            valid_ids = [by_label[s]["id"] for s in src if s in by_label]
            uncertain, reason = 0, None
            if src and len(valid_ids) < len(src):
                uncertain, reason = 1, "cites a line that does not exist"
            elif value and not valid_ids:
                uncertain, reason = 1, "no source line cited"
            elif value and fld not in ("action",) and not _value_in_lines(value, [by_label[s]["redacted_text"] for s in src if s in by_label]):
                uncertain, reason = 1, "value not found in the cited lines"
            if fld in ALWAYS_CONFIRM and not value:
                uncertain, reason = 1, f"{fld} not stated in the document"
            conn.execute(
                "INSERT INTO extracted_items (extraction_id, group_kind, group_index, field, value, source_line_ids, "
                "uncertain, uncertain_reason, requires_confirm, suggestion_json, caregiver_value) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ext_id, kind, idx, fld, value, json.dumps(valid_ids), uncertain, reason, int(requires),
                 json.dumps(suggestion) if suggestion else None, value))

        for i, med in enumerate(data.get("medications") or []):
            src = [str(s) for s in (med.get("source_lines") or [])]
            action = str(med.get("action") or "unclear").lower()
            if action not in ACTIONS:
                action = "unclear"
            med["action"] = action
            norm = drugs.normalize(str(med.get("name") or "")) if med.get("name") else None
            for fld in MED_FIELDS:
                add("medication", i, fld, med.get(fld), src, requires=fld in ALWAYS_CONFIRM,
                    suggestion=norm.as_dict() if (fld == "name" and norm) else None)
            if action == "stop":
                # a stop must be backed by stop wording in the cited lines
                texts = " ".join(by_label[s]["redacted_text"] for s in src if s in by_label).lower()
                if not re.search(r"\b(stop|discontinu|withh[oe]ld|do not (take|continue)|omit|cease)", texts):
                    conn.execute(
                        "UPDATE extracted_items SET uncertain=1, uncertain_reason='no stop instruction in the cited lines' "
                        "WHERE extraction_id=? AND group_kind='medication' AND group_index=? AND field='action'",
                        (ext_id, i))
            if action == "unclear":
                conn.execute(
                    "UPDATE extracted_items SET uncertain=1, uncertain_reason='start/continue/stop not clear' "
                    "WHERE extraction_id=? AND group_kind='medication' AND group_index=? AND field='action'",
                    (ext_id, i))
        for i, a in enumerate(data.get("allergies") or []):
            src = [str(s) for s in (a.get("source_lines") or [])]
            add("allergy", i, "substance", a.get("substance"), src, requires=True)
            add("allergy", i, "reaction", a.get("reaction"), src)
        for i, f in enumerate(data.get("follow_ups") or []):
            src = [str(s) for s in (f.get("source_lines") or [])]
            add("follow_up", i, "what", f.get("what"), src)
            add("follow_up", i, "when", f.get("when"), src)
        for i, o in enumerate(data.get("other_instructions") or []):
            src = [str(s) for s in (o.get("source_lines") or [])]
            add("instruction", i, "text", o.get("text"), src)
        conn.execute("UPDATE documents SET status='extracted' WHERE id=?", (doc_id,))
        db.event(conn, "document_extracted", f"Read document #{doc_id}: "
                 f"{len(data.get('medications') or [])} medication entries awaiting confirmation",
                 ref_table="extractions", ref_id=ext_id)
    return ext_id


def vision_fallback(conn: sqlite3.Connection, ws_dir: Path, doc_id: int, page: int) -> int:
    """Opt-in: send one page image, with detected identifier lines masked, to the vision model to read its text."""
    if not settings.allow_vision_fallback:
        raise PermissionError("Vision fallback is disabled on this server")
    img = ws_dir / "uploads" / f"doc-{doc_id}" / f"page-{page}.png"
    if not img.exists():
        raise LookupError("page image not found")
    redactor = Redactor(_person_names(conn))
    boxes = [tuple(json.loads(r["bbox"])) for r in conn.execute(
        "SELECT text, bbox FROM ocr_lines WHERE document_id=? AND page=?", (doc_id, page)).fetchall()
        if redactor.changed(r["text"])]
    png = ocr.masked_page_png(img, boxes)  # type: ignore[arg-type]
    b64 = base64.b64encode(png).decode()
    messages = [
        {"role": "system", "content": "Transcribe the printed text of this medical document page line by line. "
                                      "Output only the text, one line per line. Do not transcribe names, phone "
                                      "numbers, addresses or ID numbers; write [REDACTED] instead."},
        {"role": "user", "content": [
            {"type": "text", "text": "Transcribe this page."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]},
    ]
    text, run_id = llm.chat(conn, "vision_transcribe", settings.model_vision, messages, max_tokens=4000)
    text = llm._THINK.sub("", text)
    with db.tx(conn):
        conn.execute("DELETE FROM ocr_lines WHERE document_id=? AND page=?", (doc_id, page))
        for i, raw in enumerate([t for t in text.splitlines() if t.strip()], start=1):
            conn.execute(
                "INSERT INTO ocr_lines (document_id, page, line_no, text, bbox, source, redacted_text) "
                "VALUES (?,?,?,?,?,?,?)",
                (doc_id, page, i, raw.strip(), json.dumps([0, 0, 1, 1]), "vision", redactor.redact(raw.strip())))
        db.event(conn, "vision_fallback", f"Page {page} of document #{doc_id} read by the vision model (opt-in)",
                 ref_table="model_runs", ref_id=run_id)
    return run_id
