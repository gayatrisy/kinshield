"""End-to-end demo story with fake external services."""
from __future__ import annotations

from datetime import timedelta

from kinshield import db, notify, worker


def _confirm_all(client, ext_id, overrides=None, drop=()):
    ext = client.get(f"/api/extractions/{ext_id}").json()
    items, groups = [], []
    for g in ext["groups"]:
        groups.append({"kind": g["kind"], "index": g["index"], "include": (g["kind"], g["index"]) not in drop})
        for it in g["items"]:
            v = (overrides or {}).get((g["kind"], g["index"], it["field"]), it["caregiver_value"])
            items.append({"id": it["id"], "value": v, "confirmed": True})
    return client.post(f"/api/extractions/{ext_id}/confirm", json={"items": items, "groups": groups})


def test_full_demo_story(client, fakes):
    # 1. intake of the synthetic discharge PDF
    r = client.post("/api/documents/demo", json={"variant": "pdf"})
    assert r.status_code == 200, r.text
    ext_id = r.json()["extraction_id"]
    ext = client.get(f"/api/extractions/{ext_id}").json()
    meds = [g for g in ext["groups"] if g["kind"] == "medication"]
    assert len(meds) == 7
    names = {next(i["value"] for i in g["items"] if i["field"] == "name") for g in meds}
    assert {"Clopidogrel", "Omeprazole", "Ibuprofen"} <= names

    # redaction: the prompt sent to the model contains no identifiers
    act = client.get("/api/activity").json()
    prompt = next(r["redacted_prompt"] for r in act["model_runs"] if r["task"] == "extract_document")
    for secret in ("Ramesh", "Kulkarni", "98220", "CCH-2026", "Shanti Nagar", "1952", "Meera"):
        assert secret not in prompt, secret
    assert "Clopidogrel" in prompt

    # 2. confirmation is mandatory for name/strength/dose
    bad = client.post(f"/api/extractions/{ext_id}/confirm", json={"items": [], "groups": []})
    assert bad.status_code == 422
    r = _confirm_all(client, ext_id, overrides={("medication", 6, "action"): "stop"})
    assert r.status_code == 200, r.text

    # 3. reconciliation
    f = {x["kind"]: x for x in client.get("/api/findings?pending=true").json()}
    kinds = sorted(x["kind"] for x in client.get("/api/findings").json())
    assert kinds.count("new") == 3  # clopidogrel, metoprolol, omeprazole
    assert kinds.count("changed") == 2  # amlodipine, atorvastatin
    assert "explicitly_stopped" in kinds and "not_mentioned" in kinds and "unchanged" in kinds
    nm = f["not_mentioned"]
    assert nm["detail"]["current"]["name_as_written"].startswith("Metformin")
    # a med that's not mentioned is still active until the caregiver decides
    active = [m for m in client.get("/api/medications").json() if m["status"] == "active"]
    assert any(m["name_as_written"].startswith("Metformin") for m in active)

    for x in client.get("/api/findings?pending=true").json():
        choice = {"new": "accept", "changed": "accept", "explicitly_stopped": "stop",
                  "not_mentioned": "ask_doctor"}[x["kind"]]
        r = client.post(f"/api/findings/{x['id']}/decide", json={"decision": choice})
        assert r.status_code == 200, r.text
    meds = {m["name_as_written"]: m for m in client.get("/api/medications").json()}
    assert meds["Ibuprofen"]["status"] == "stopped"
    assert meds["Metformin"]["status"] == "active" and meds["Metformin"]["ask_doctor"] == 1
    assert next(m for m in meds.values() if m["name_as_written"].startswith("Amlodipine"))["strength"] == "10 mg"
    assert meds["Clopidogrel"]["confirmed_by"]["name"] == "Anjali Kulkarni"

    # 4. label evidence (ran in background task): three distinct outcomes
    clop = meds["Clopidogrel"]
    outcomes = {e["outcome"] for e in clop["evidence"]}
    assert "mention_found" in outcomes
    omep = next(e for e in clop["evidence"] if e.get("other_name", "").startswith("Omeprazole"))
    assert "omeprazole" in omep["passage"].lower()
    metop = next(m for n, m in meds.items() if n.startswith("Metoprolol"))
    assert [e["outcome"] for e in metop["evidence"]] == ["no_label"]

    # 5. reminders and escalation with honest status
    ack_id = client.post("/api/demo/due-now", json={"medication_id": clop["id"]}).json()["acknowledgement_id"]
    ws_id = client.get("/api/session").json()["workspace"]
    conn = db.open_workspace(ws_id)
    later = db.now() + timedelta(minutes=3)
    stats = worker.tick_workspace(conn, ws_id, at=later)
    assert stats["escalated"] >= 1
    n = db.one(conn.execute("SELECT * FROM notifications WHERE acknowledgement_id=?", (ack_id,)))
    assert n["api_status"] == "accepted" and n["seen_at"] is None
    assert notify.describe(n) == "Sent to Telegram, not yet seen"
    assert "Seen" in fakes.sent[-1]["reply_markup"]["inline_keyboard"][0][0]["text"]
    assert "does not show whether a dose was taken" in fakes.sent[-1]["text"]

    # Seen tap via Telegram callback
    data = fakes.sent[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    fakes.updates = [{"update_id": 5, "callback_query": {"id": "cq1", "data": data}}]
    notify.poll_updates(db.open_workspace)
    n = db.one(conn.execute("SELECT * FROM notifications WHERE id=?", (n["id"],)))
    assert n["seen_at"] is not None
    # forged callback is ignored
    assert notify.parse_seen_callback(data[:-1] + "x") is None
    conn.close()

    # 6. ask + brief, uncited sentences dropped
    r = client.post("/api/ask", json={"question": "What changed this week?"}).json()
    assert r["dropped_uncited"] == 1 and all(a["cites"] for a in r["answer"])
    b = client.post("/api/brief").json()
    assert b["dropped_uncited"] >= 1
    met = f"med:{meds['Metformin']['id']}"
    assert any(met in q["cites"] for q in b["questions"])  # ask_doctor medicine always raised
    pdf = client.get(b["pdf"])
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    # 7. every confirmation recorded with an actor
    act = client.get("/api/activity").json()
    assert len(act["confirmations"]) > 20 and all(c["actor"] for c in act["confirmations"])
    assert act["totals"]["cost_usd"] > 0


def test_photo_path_uses_ocr(client):
    r = client.post("/api/documents/demo", json={"variant": "photo"})
    assert r.status_code == 200, r.text
    ext = client.get(f"/api/extractions/{r.json()['extraction_id']}").json()
    assert ext["document"]["has_text_layer"] == 0
    assert all(l["source"] == "ocr" for l in ext["lines"])
    assert len([g for g in ext["groups"] if g["kind"] == "medication"]) >= 5


def test_failed_telegram_is_recorded(client, fakes):
    fakes.fail = True
    ws_id = client.get("/api/session").json()["workspace"]
    ack = client.get("/api/parent/today").json()["items"]
    meds = client.get("/api/medications").json()
    a = client.post("/api/demo/due-now", json={"medication_id": meds[0]["id"]}).json()["acknowledgement_id"]
    r = client.post(f"/api/acks/{a}", json={"status": "help"}).json()
    conn = db.open_workspace(ws_id)
    n = db.one(conn.execute("SELECT * FROM notifications WHERE id=?", (r["notification_id"],)))
    conn.close()
    assert n["api_status"] == "failed" and "blocked" in n["error"]
    assert notify.describe(n) == "Failed to send"
    assert isinstance(ack, list)


def test_workspaces_are_isolated(client):
    from fastapi.testclient import TestClient
    from kinshield.api import app

    other = TestClient(app)
    other.post("/api/session/demo")
    client.post("/api/documents/demo", json={"variant": "pdf"})
    assert client.get("/api/documents").json()
    assert other.get("/api/documents").json() == []
    assert TestClient(app).get("/api/documents").status_code == 401
    doc_id = client.get("/api/documents").json()[0]["id"]
    assert other.get(f"/api/documents/{doc_id}/pages/1").status_code == 404


def test_stop_needs_stop_wording():
    from kinshield import intake
    conn = db.open_workspace("unit-stop")
    from kinshield import demo
    demo.seed(conn, "unit-stop")
    doc = conn.execute("INSERT INTO documents (person_id, file_path, original_name, kind, uploaded_at) "
                       "VALUES (2,'x','x','t','t')").lastrowid
    lid = conn.execute("INSERT INTO ocr_lines (document_id, page, line_no, text, bbox, source, redacted_text) "
                       "VALUES (?,1,1,'Tab Aspirin 75 mg OD','[0,0,1,1]','ocr','Tab Aspirin 75 mg OD')", (doc,)).lastrowid
    data = {"medications": [{"name": "Aspirin", "strength": "75 mg", "dose": "1 tablet", "action": "stop",
                             "source_lines": [f"L{lid}"]}]}
    run = conn.execute("INSERT INTO model_runs (task, model_id, redacted_prompt, created_at) VALUES ('t','m','p','t')").lastrowid
    ext = intake._store_extraction(conn, doc, data, run, {f"L{lid}": {"id": lid, "redacted_text": "Tab Aspirin 75 mg OD"}})
    act = db.one(conn.execute("SELECT * FROM extracted_items WHERE extraction_id=? AND field='action'", (ext,)))
    assert act["uncertain"] == 1 and "stop" in act["uncertain_reason"]
    dose = db.one(conn.execute("SELECT * FROM extracted_items WHERE extraction_id=? AND field='dose'", (ext,)))
    assert dose["uncertain"] == 1  # "1 tablet" is not in the cited line
    conn.close()


def test_uncited_medicine_line_is_flagged():
    """Live Nano once skipped 'Cap Omeprazole 20 mg'; a line like that must be flagged, not lost."""
    from kinshield import demo, intake
    conn = db.open_workspace("unit-missed")
    demo.seed(conn, "unit-missed")
    doc = conn.execute("INSERT INTO documents (person_id, file_path, original_name, kind, uploaded_at) "
                       "VALUES (2,'x','x','t','t')").lastrowid
    ids = {}
    for i, t in enumerate(["Tab Clopidogrel 75 mg 1-0-0", "Cap Omeprazole 20 mg 1-0-0", "Walk daily"], 1):
        ids[i] = conn.execute("INSERT INTO ocr_lines (document_id, page, line_no, text, bbox, source, redacted_text) "
                              "VALUES (?,1,?,?,'[0,0,1,1]','ocr',?)", (doc, i, t, t)).lastrowid
    by_label = {f"L{v}": {"id": v, "redacted_text": t} for v, t in
                zip(ids.values(), ["Tab Clopidogrel 75 mg 1-0-0", "Cap Omeprazole 20 mg 1-0-0", "Walk daily"])}
    data = {"medications": [{"name": "Clopidogrel", "strength": "75 mg", "dose": "1-0-0", "action": "start",
                             "source_lines": [f"L{ids[1]}"]}]}
    run = conn.execute("INSERT INTO model_runs (task, model_id, redacted_prompt, created_at) VALUES ('t','m','p','t')").lastrowid
    ext = intake._store_extraction(conn, doc, data, run, by_label)
    flagged = db.rows(conn.execute("SELECT * FROM extracted_items WHERE extraction_id=? AND group_kind='possible_missed'", (ext,)))
    assert [f["value"] for f in flagged] == ["Cap Omeprazole 20 mg 1-0-0"]
    assert flagged[0]["requires_confirm"] == 1
    conn.close()


def test_header_and_link_token_work_without_cookie(client):
    from fastapi.testclient import TestClient
    from kinshield.api import app

    token = client.post("/api/session/demo").json()["token"]
    bare = TestClient(app)  # no cookies, like a browser blocking third-party cookies in an iframe
    assert bare.get("/api/session", headers={"X-KinShield-Token": token}).status_code == 200
    assert bare.get(f"/api/documents?t={token}").status_code == 200
    assert bare.get("/api/documents").status_code == 401
