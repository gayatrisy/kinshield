"""HTTP API and static web app."""
from __future__ import annotations

import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import brief, careplan, db, demo, evidence, intake, llm, memory, notify, ocr, reconcile, worker
from .config import settings

app = FastAPI(title="KinShield", version="1.0.0")
COOKIE = "ks_ws"
MAX_UPLOAD = 15 * 1024 * 1024
ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


class Workspace:
    def __init__(self, ws_id: str) -> None:
        self.id = ws_id
        self.dir = db.workspace_dir(ws_id)
        self.conn = db.open_workspace(ws_id)


def get_ws(request: Request) -> Iterator[Workspace]:
    token = request.cookies.get(COOKIE) or request.headers.get("X-KinShield-Token", "")
    ws_id = db.resolve_token(token)
    if not ws_id:
        raise HTTPException(401, "No active workspace. Start a demo or set up KinShield.")
    ws = Workspace(ws_id)
    try:
        yield ws
    finally:
        ws.conn.close()


@app.exception_handler(llm.RateLimited)
async def _rate(_: Request, exc: llm.RateLimited) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=429)


@app.exception_handler(llm.LLMError)
async def _llm(_: Request, exc: llm.LLMError) -> JSONResponse:
    return JSONResponse({"detail": f"Model call failed: {exc}"}, status_code=502)


@app.exception_handler(reconcile.ConfirmationError)
async def _conf(_: Request, exc: reconcile.ConfirmationError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=422)


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=settings.workspace_ttl_days * 86400,
                    secure=settings.public_url.startswith("https"))


# ---------- session ----------

class OwnerSetup(BaseModel):
    caregiver: str = Field(min_length=1, max_length=80)
    parent: str = Field(min_length=1, max_length=80)
    timezone: str = "Asia/Kolkata"


@app.post("/api/session/demo")
def start_demo(response: Response) -> dict[str, Any]:
    ws_id, token = db.create_workspace(is_demo=True)
    conn = db.open_workspace(ws_id)
    try:
        demo.seed(conn, ws_id)
    finally:
        conn.close()
    _set_cookie(response, token)
    return {"workspace": ws_id, "token": token}


@app.post("/api/session/owner")
def start_owner(body: OwnerSetup, response: Response) -> dict[str, Any]:
    ws_id, token = db.create_workspace(is_demo=False)
    conn = db.open_workspace(ws_id)
    try:
        demo.setup_owner(conn, ws_id, body.caregiver, body.parent, body.timezone)
    finally:
        conn.close()
    _set_cookie(response, token)
    return {"workspace": ws_id, "token": token}


@app.get("/api/session")
def session(ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    reg = db.registry()
    meta = db.one(reg.execute("SELECT created_at, expires_at, is_demo FROM workspaces WHERE id=?", (ws.id,)))
    reg.close()
    people = db.rows(ws.conn.execute("SELECT id, role, name, timezone, telegram_chat_id IS NOT NULL AS linked, "
                                     "telegram_link_code FROM people"))
    care = next((p for p in people if p["role"] == "caregiver"), None)
    return {
        "workspace": ws.id, **(meta or {}), "people": [{k: v for k, v in p.items() if k != "telegram_link_code"} for p in people],
        "telegram": {"configured": bool(settings.telegram_token), "linked": bool(care and care["linked"]),
                     "demo_channel": bool(settings.telegram_demo_chat_id),
                     "link_url": notify.link_url(care["telegram_link_code"]) if care and care["telegram_link_code"] else None},
        "models": {"fast": settings.model_fast, "main": settings.model_main, "deep": settings.model_deep,
                   "vision": settings.model_vision, "configured": bool(settings.nebius_api_key)},
        "demo_mode": settings.demo_mode, "ack_window_minutes": settings.default_ack_window_minutes,
        "vision_fallback": settings.allow_vision_fallback,
    }


@app.delete("/api/session")
def delete_everything(response: Response, ws: Workspace = Depends(get_ws)) -> dict[str, str]:
    ws.conn.close()
    db.delete_workspace(ws.id)
    response.delete_cookie(COOKIE)
    return {"status": "deleted"}


@app.post("/api/session/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(COOKIE)
    return {"status": "ok"}


@app.get("/api/export")
def export(ws: Workspace = Depends(get_ws)) -> StreamingResponse:
    ws.conn.execute("PRAGMA wal_checkpoint(FULL)")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in ws.dir.rglob("*"):
            if p.is_file() and not p.name.endswith(("-wal", "-shm")):
                z.write(p, p.relative_to(ws.dir))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": "attachment; filename=kinshield-export.zip"})


# ---------- dashboard ----------

def _tz(conn: sqlite3.Connection):
    return worker._tz(conn)


def _acks_between(conn: sqlite3.Connection, start: datetime, end: datetime) -> list[dict[str, Any]]:
    return db.rows(conn.execute(
        "SELECT a.*, m.id AS medication_id, m.name_as_written, m.strength, m.dose, m.instructions FROM acknowledgements a "
        "JOIN schedules s ON s.id=a.schedule_id JOIN medications m ON m.id=s.medication_id "
        "WHERE a.due_at >= ? AND a.due_at < ? AND a.status != 'cancelled' ORDER BY a.due_at",
        (db.iso(start), db.iso(end))))


def _today_bounds(conn: sqlite3.Connection) -> tuple[datetime, datetime]:
    tz = _tz(conn)
    local = db.now().astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _notif(n: dict[str, Any]) -> dict[str, Any]:
    return {**n, "status_text": notify.describe(n)}


@app.get("/api/dashboard")
def dashboard(ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    c = ws.conn
    worker.plan_acknowledgements(c)
    start, end = _today_bounds(c)
    today = _acks_between(c, start, end)
    pending_ext = db.rows(c.execute(
        "SELECT e.id, e.document_id, d.original_name, e.created_at FROM extractions e JOIN documents d ON d.id=e.document_id "
        "WHERE e.status='pending' ORDER BY e.id DESC"))
    pending_findings = c.execute("SELECT COUNT(*) FROM reconciliation_findings WHERE decision IS NULL").fetchone()[0]
    needs_attention = [a for a in today if a["status"] in ("no_response", "help")]
    notifs = [_notif(n) for n in db.rows(c.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 10"))]
    events = db.rows(c.execute("SELECT * FROM timeline_events ORDER BY id DESC LIMIT 15"))
    meds = careplan.active_meds(c)
    ev_flags = db.rows(c.execute(
        "SELECT e.id, e.medication_id, m.name_as_written AS med, o.name_as_written AS other FROM evidence e "
        "JOIN medications m ON m.id=e.medication_id LEFT JOIN medications o ON o.id=e.other_medication_id "
        "WHERE e.outcome='mention_found' AND m.status='active'"))
    return {"today": today, "pending_extractions": pending_ext, "pending_findings": pending_findings,
            "needs_attention": needs_attention, "notifications": notifs, "events": events,
            "active_medication_count": len(meds), "label_mentions": ev_flags,
            "people": db.rows(c.execute("SELECT id, role, name FROM people"))}


# ---------- documents & intake ----------

def _process(ws: Workspace, doc_id: int) -> dict[str, Any]:
    try:
        intake.ingest(ws.conn, ws.dir, doc_id)
    except ocr.OCRUnavailable as exc:
        ws.conn.execute("UPDATE documents SET status='failed' WHERE id=?", (doc_id,))
        raise HTTPException(503, str(exc)) from exc
    try:
        ext_id = intake.extract(ws.conn, doc_id)
    except (llm.LLMError, ValueError) as exc:
        ws.conn.execute("UPDATE documents SET status='failed' WHERE id=?", (doc_id,))
        db.event(ws.conn, "extraction_failed", f"Could not extract document #{doc_id}: {exc}"[:300],
                 ref_table="documents", ref_id=doc_id)
        if isinstance(exc, llm.RateLimited):
            raise
        raise HTTPException(502, f"The document was read locally, but extraction failed: {exc}") from exc
    return {"document_id": doc_id, "extraction_id": ext_id}


def _store_upload(ws: Workspace, name: str, data: bytes, kind: str) -> int:
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(415, "Upload a PDF or a JPG/PNG/WEBP photo")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (max 15 MB)")
    cur = ws.conn.execute(
        "INSERT INTO documents (person_id, file_path, original_name, kind, uploaded_at) VALUES (?,?,?,?,?)",
        (db.parent_id(ws.conn), "", re.sub(r"[^\w.\- ]", "_", name)[:120], kind, db.iso()))
    doc_id = int(cur.lastrowid)
    path = ws.dir / "uploads" / f"original-{doc_id}{suffix}"
    path.write_bytes(data)
    ws.conn.execute("UPDATE documents SET file_path=? WHERE id=?", (str(path), doc_id))
    db.event(ws.conn, "document_uploaded", f"Uploaded {name}", ref_table="documents", ref_id=doc_id)
    return doc_id


@app.post("/api/documents")
async def upload(file: UploadFile = File(...), ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    data = await file.read()
    doc_id = _store_upload(ws, file.filename or "upload", data, "upload")
    return _process(ws, doc_id)


class DemoDoc(BaseModel):
    variant: str = "pdf"


@app.post("/api/documents/demo")
def upload_demo(body: DemoDoc, ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    files = demo.ensure_demo_files(settings.data_dir)
    src = files["photo"] if body.variant == "photo" else files["pdf"]
    doc_id = _store_upload(ws, src.name, src.read_bytes(), "demo")
    return _process(ws, doc_id)


@app.get("/api/documents")
def list_documents(ws: Workspace = Depends(get_ws)) -> list[dict[str, Any]]:
    docs = db.rows(ws.conn.execute("SELECT id, original_name, kind, uploaded_at, page_count, has_text_layer, status "
                                   "FROM documents ORDER BY id DESC"))
    for d in docs:
        e = db.one(ws.conn.execute("SELECT id, status FROM extractions WHERE document_id=? ORDER BY id DESC LIMIT 1",
                                   (d["id"],)))
        d["extraction"] = e
    return docs


@app.post("/api/documents/{doc_id}/extract")
def retry_extract(doc_id: int, ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    return {"document_id": doc_id, "extraction_id": intake.extract(ws.conn, doc_id)}


@app.post("/api/documents/{doc_id}/vision")
def vision(doc_id: int, page: int = 1, ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    try:
        run_id = intake.vision_fallback(ws.conn, ws.dir, doc_id, page)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"model_run_id": run_id, "extraction_id": intake.extract(ws.conn, doc_id)}


@app.get("/api/documents/{doc_id}/pages/{page}")
def page_image(doc_id: int, page: int, ws: Workspace = Depends(get_ws)) -> FileResponse:
    p = ws.dir / "uploads" / f"doc-{doc_id}" / f"page-{page}.png"
    if not p.exists():
        raise HTTPException(404, "page not found")
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/extractions/{ext_id}")
def get_extraction(ext_id: int, ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    c = ws.conn
    ext = db.one(c.execute("SELECT * FROM extractions WHERE id=?", (ext_id,)))
    if not ext:
        raise HTTPException(404, "not found")
    doc = db.one(c.execute("SELECT id, original_name, page_count, has_text_layer FROM documents WHERE id=?",
                           (ext["document_id"],)))
    lines = {l["id"]: l for l in db.rows(c.execute(
        "SELECT id, page, line_no, text, bbox, source FROM ocr_lines WHERE document_id=?", (ext["document_id"],)))}
    for l in lines.values():
        l["bbox"] = json.loads(l["bbox"])
    items = db.rows(c.execute("SELECT * FROM extracted_items WHERE extraction_id=? ORDER BY group_kind, group_index, id",
                              (ext_id,)))
    groups: dict[str, dict[str, Any]] = {}
    for it in items:
        it["source_line_ids"] = json.loads(it["source_line_ids"])
        it["suggestion"] = json.loads(it["suggestion_json"]) if it["suggestion_json"] else None
        key = f"{it['group_kind']}:{it['group_index']}"
        g = groups.setdefault(key, {"kind": it["group_kind"], "index": it["group_index"], "items": []})
        g["items"].append(it)
    for g in groups.values():
        if g["kind"] == "medication":
            f = {i["field"]: i["caregiver_value"] for i in g["items"]}
            g["suggested_times"] = careplan.times_from_frequency(f.get("frequency"), f.get("timing"))
            name_item = next((i for i in g["items"] if i["field"] == "name"), None)
            g["normalization"] = name_item["suggestion"] if name_item else None
    run = db.one(c.execute("SELECT id, model_id, prompt_tokens, completion_tokens, latency_ms, cost_usd FROM model_runs "
                           "WHERE id=?", (ext["model_run_id"],)))
    return {"extraction": {k: ext[k] for k in ("id", "document_id", "status", "created_at")}, "document": doc,
            "lines": list(lines.values()), "groups": list(groups.values()), "model_run": run}


@app.post("/api/extractions/{ext_id}/confirm")
def confirm_extraction(ext_id: int, payload: dict[str, Any], ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    ids = reconcile.confirm_extraction(ws.conn, ext_id, payload)
    return {"finding_ids": ids}


@app.post("/api/extractions/{ext_id}/reject")
def reject_extraction(ext_id: int, ws: Workspace = Depends(get_ws)) -> dict[str, str]:
    c = ws.conn
    with db.tx(c):
        cur = c.execute("UPDATE extractions SET status='rejected' WHERE id=? AND status='pending'", (ext_id,))
        if cur.rowcount:
            db.confirm(c, db.caregiver_id(c), "extractions", ext_id, "reject")
            db.event(c, "extraction_rejected", f"Caregiver rejected extraction #{ext_id}", ref_table="extractions",
                     ref_id=ext_id)
    return {"status": "rejected"}


# ---------- reconciliation ----------

@app.get("/api/findings")
def findings(pending: bool = False, ws: Workspace = Depends(get_ws)) -> list[dict[str, Any]]:
    q = "SELECT * FROM reconciliation_findings" + (" WHERE decision IS NULL" if pending else "") + " ORDER BY id DESC LIMIT 200"
    out = db.rows(ws.conn.execute(q))
    for f in out:
        f["detail"] = json.loads(f.pop("detail_json"))
        f["source_line_ids"] = json.loads(f["source_line_ids"])
        f["choices"] = sorted(reconcile.DECISIONS[f["kind"]])
        f["sources"] = db.rows(ws.conn.execute(
            f"SELECT id, page, text FROM ocr_lines WHERE id IN ({','.join('?' * len(f['source_line_ids'])) or 'NULL'})",
            f["source_line_ids"])) if f["source_line_ids"] else []
    return out


class Decision(BaseModel):
    decision: str


def _review_in_background(ws_id: str, med_ids: list[int]) -> None:
    conn = db.open_workspace(ws_id)
    try:
        for mid in med_ids:
            try:
                evidence.review(conn, mid)
            except Exception as exc:  # recorded so the UI can show it
                db.event(conn, "label_review_failed", f"Label review failed for medication #{mid}: {exc}"[:300])
    finally:
        conn.close()


@app.post("/api/findings/{finding_id}/decide")
def decide(finding_id: int, body: Decision, bg: BackgroundTasks, ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    out = reconcile.decide(ws.conn, finding_id, body.decision)
    if out["new_medication_ids"]:
        bg.add_task(_review_in_background, ws.id, out["new_medication_ids"])
    return out


# ---------- medications & evidence ----------

@app.get("/api/medications")
def medications(ws: Workspace = Depends(get_ws)) -> list[dict[str, Any]]:
    c = ws.conn
    meds = db.rows(c.execute("SELECT * FROM medications ORDER BY status, id"))
    for m in meds:
        s = db.one(c.execute("SELECT * FROM schedules WHERE medication_id=? AND active=1", (m["id"],)))
        m["times"] = json.loads(s["times"]) if s else []
        m["evidence"] = db.rows(c.execute(
            "SELECT e.*, o.name_as_written AS other_name FROM evidence e LEFT JOIN medications o ON o.id=e.other_medication_id "
            "WHERE e.medication_id=? ORDER BY e.outcome, e.id", (m["id"],)))
        for e in m["evidence"]:
            e["outcome_text"] = evidence.OUTCOME_TEXT[e["outcome"]].format(other=e.get("other_name") or "another medicine")
        conf = db.one(c.execute("SELECT c.at, c.action, p.name FROM confirmations c JOIN people p ON p.id=c.actor_person_id "
                                "WHERE c.id=?", (m["confirmation_id"],))) if m["confirmation_id"] else None
        m["confirmed_by"] = conf
    return meds


@app.post("/api/medications/{med_id}/review")
def review_med(med_id: int, ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    return {"evidence_ids": evidence.review(ws.conn, med_id), "note": evidence.NOT_SAFETY}


class Times(BaseModel):
    times: list[str]


@app.put("/api/medications/{med_id}/times")
def set_times(med_id: int, body: Times, ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    c = ws.conn
    with db.tx(c):
        before = careplan.snapshot(c)
        careplan.set_schedule(c, med_id, body.times)
        cid = db.confirm(c, db.caregiver_id(c), "schedules", med_id, "edit", before=before, after=careplan.snapshot(c))
        careplan.new_version(c, f"Reminder times changed for medication #{med_id}", [cid])
    return {"times": careplan.valid_times(body.times)}


@app.get("/api/plan/versions")
def plan_versions(ws: Workspace = Depends(get_ws)) -> list[dict[str, Any]]:
    out = db.rows(ws.conn.execute("SELECT * FROM care_plan_versions ORDER BY id DESC LIMIT 50"))
    for v in out:
        v["snapshot"] = json.loads(v.pop("snapshot_json"))
        v["confirmation_ids"] = json.loads(v["confirmation_ids"])
    return out


# ---------- parent view ----------

@app.get("/api/parent/today")
def parent_today(ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    c = ws.conn
    worker.plan_acknowledgements(c)
    start, end = _today_bounds(c)
    parent = db.one(c.execute("SELECT name FROM people WHERE role='parent' LIMIT 1"))
    return {"name": (parent or {}).get("name", ""), "items": _acks_between(c, start, end), "now": db.iso()}


class AckBody(BaseModel):
    status: str


@app.post("/api/acks/{ack_id}")
def respond(ack_id: int, body: AckBody, ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    if body.status not in ("acknowledged", "skipped", "help"):
        raise HTTPException(422, "status must be acknowledged, skipped or help")
    c = ws.conn
    ack = db.one(c.execute("SELECT * FROM acknowledgements WHERE id=?", (ack_id,)))
    if not ack:
        raise HTTPException(404, "not found")
    if ack["status"] not in ("pending", "no_response"):
        raise HTTPException(409, "already answered")
    c.execute("UPDATE acknowledgements SET status=?, responded_at=? WHERE id=?", (body.status, db.iso(), ack_id))
    label = {"acknowledged": "said they took it", "skipped": "chose to skip", "help": "asked for help"}[body.status]
    db.event(c, "acknowledgement", f"Parent {label} (reminder #{ack_id})", person_id=db.parent_id(c),
             ref_table="acknowledgements", ref_id=ack_id)
    nid = None
    if body.status == "help":
        nid = worker.escalate(c, ws.id, {**ack, "status": "help"}, "Your parent tapped 'I need help'")
    return {"status": body.status, "notification_id": nid}


class DueNow(BaseModel):
    medication_id: int


@app.post("/api/demo/due-now")
def due_now(body: DueNow, ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    """Demo helper: make a reminder due right now so escalation can be shown without waiting for its real time."""
    c = ws.conn
    s = db.one(c.execute("SELECT * FROM schedules WHERE medication_id=? AND active=1", (body.medication_id,)))
    if not s:
        raise HTTPException(404, "This medicine has no active reminder schedule")
    window = settings.default_ack_window_minutes
    cur = c.execute("INSERT OR IGNORE INTO acknowledgements (schedule_id, due_at, window_minutes, reminded_at) "
                    "VALUES (?,?,?,?)", (s["id"], db.iso(), window, db.iso()))
    db.event(c, "demo", f"Demo: reminder made due now for medication #{body.medication_id} "
             f"({window}-minute response window)", ref_table="acknowledgements", ref_id=cur.lastrowid)
    return {"acknowledgement_id": cur.lastrowid, "window_minutes": window}


# ---------- memory: symptoms, ask, brief ----------

class Symptom(BaseModel):
    text: str = Field(min_length=2, max_length=500)


@app.post("/api/symptoms")
def symptom(body: Symptom, ws: Workspace = Depends(get_ws)) -> dict[str, int]:
    return {"event_id": memory.add_symptom(ws.conn, body.text)}


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=500)


@app.post("/api/ask")
def ask(body: Question, ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    return memory.ask(ws.conn, body.question)


@app.post("/api/brief")
def make_brief(ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    out = brief.build(ws.conn, ws.dir)
    return {k: v for k, v in out.items() if k != "records"} | {"pdf": f"/api/brief/{out['id']}.pdf"}


@app.get("/api/brief/latest")
def latest_brief(ws: Workspace = Depends(get_ws)) -> dict[str, Any] | None:
    b = brief.latest(ws.conn)
    if not b:
        return None
    cited = {c for part in (b["summary"], b["questions"]) for it in part for c in it["cites"]}
    return {k: v for k, v in b.items() if k != "records"} | {"records": {c: b["records"][c] for c in cited},
                                                             "pdf": f"/api/brief/{b['id']}.pdf"}


@app.get("/api/brief/{brief_id}.pdf")
def brief_pdf(brief_id: int, ws: Workspace = Depends(get_ws)) -> FileResponse:
    r = db.one(ws.conn.execute("SELECT file_path FROM briefs WHERE id=?", (brief_id,)))
    if not r or not Path(r["file_path"]).exists():
        raise HTTPException(404, "not found")
    return FileResponse(r["file_path"], media_type="application/pdf", filename=f"visit-brief-{brief_id}.pdf")


# ---------- activity ----------

@app.get("/api/activity")
def activity(ws: Workspace = Depends(get_ws)) -> dict[str, Any]:
    c = ws.conn
    runs = db.rows(c.execute("SELECT * FROM model_runs ORDER BY id DESC LIMIT 100"))
    totals = db.one(c.execute("SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
                              "COALESCE(SUM(completion_tokens),0) AS completion_tokens, COALESCE(SUM(cost_usd),0) AS cost_usd "
                              "FROM model_runs"))
    by_model = db.rows(c.execute("SELECT model_id, COUNT(*) AS calls, SUM(cost_usd) AS cost_usd, AVG(latency_ms) AS avg_ms "
                                 "FROM model_runs GROUP BY model_id"))
    confs = db.rows(c.execute("SELECT c.id, c.target_table, c.target_id, c.action, c.at, p.name AS actor, p.role "
                              "FROM confirmations c JOIN people p ON p.id=c.actor_person_id ORDER BY c.id DESC LIMIT 100"))
    notifs = [_notif(n) for n in db.rows(c.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 50"))]
    events = db.rows(c.execute("SELECT * FROM timeline_events ORDER BY id DESC LIMIT 100"))
    return {"model_runs": runs, "totals": totals, "by_model": by_model, "confirmations": confs,
            "notifications": notifs, "events": events}


@app.post("/api/notifications/{nid}/seen")
def seen_from_dashboard(nid: int, ws: Workspace = Depends(get_ws)) -> dict[str, bool]:
    return {"updated": notify.mark_seen(ws.conn, nid)}


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "models_configured": bool(settings.nebius_api_key),
            "telegram_configured": bool(settings.telegram_token)}


# ---------- static web app ----------

_static = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _static.exists():
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        target = _static / path
        if path and target.is_file() and _static in target.resolve().parents:
            return FileResponse(target)
        return FileResponse(_static / "index.html")

