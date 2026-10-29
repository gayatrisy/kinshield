"""Caregiver confirmation of an extraction, then reconciliation against the active medication list."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import careplan, db, drugs, llm
from .config import settings

DECISIONS = {
    "new": {"accept", "reject"},
    "changed": {"accept", "keep_current"},
    "explicitly_stopped": {"stop", "keep", "acknowledge"},
    "possible_duplicate": {"replace", "keep_both", "reject_new"},
    "not_mentioned": {"keep", "stop", "ask_doctor"},
    "unchanged": {"no_change"},
}


class ConfirmationError(ValueError):
    pass


def _key(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _same(a: str | None, b: str | None) -> bool:
    return _key(a) == _key(b)


def _groups(conn: sqlite3.Connection, ext_id: int) -> dict[tuple[str, int], list[dict[str, Any]]]:
    out: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for it in db.rows(conn.execute("SELECT * FROM extracted_items WHERE extraction_id=? ORDER BY id", (ext_id,))):
        out.setdefault((it["group_kind"], it["group_index"]), []).append(it)
    return out


def confirm_extraction(conn: sqlite3.Connection, ext_id: int, payload: dict[str, Any]) -> list[int]:
    """Apply the caregiver's review of an extraction and create reconciliation findings.

    payload = {
      "items": [{"id": 12, "value": "25 mg", "confirmed": true}, ...],
      "groups": [{"kind": "medication", "index": 0, "include": true, "times": ["08:00"],
                  "ingredient": "metoprolol", "rxcui": "6918", "ingredient_source": "rxnorm"}, ...]
    }
    """
    ext = db.one(conn.execute("SELECT * FROM extractions WHERE id=?", (ext_id,)))
    if not ext:
        raise LookupError("extraction not found")
    if ext["status"] != "pending":
        raise ConfirmationError("This extraction was already reviewed")
    actor = db.caregiver_id(conn)
    item_updates = {int(i["id"]): i for i in payload.get("items", [])}
    group_opts = {(g["kind"], int(g["index"])): g for g in payload.get("groups", [])}
    groups = _groups(conn, ext_id)

    # Validate first: nothing is written unless every required confirmation is present.
    problems = []
    for (kind, idx), items in groups.items():
        opt = group_opts.get((kind, idx), {})
        if not opt.get("include", True):
            continue
        group_action = next((item_updates.get(i["id"], {}).get("value", i["caregiver_value"])
                             for i in items if i["field"] == "action"), None)
        for it in items:
            upd = item_updates.get(it["id"], {})
            needs = it["requires_confirm"] or it["uncertain"]
            value = upd.get("value", it["caregiver_value"])
            if needs and not upd.get("confirmed"):
                problems.append(f"{kind} {idx + 1}: confirm '{it['field']}'")
            required = ("name",) if group_action == "stop" else ("name", "dose")
            if kind == "medication" and it["field"] in required and not (value or "").strip():
                problems.append(f"medication {idx + 1}: '{it['field']}' is empty")
            if kind == "medication" and it["field"] == "action" and (value or "") not in (
                    "start", "continue", "change", "stop"):
                problems.append(f"medication {idx + 1}: choose start, continue, change or stop")
    if problems:
        raise ConfirmationError("; ".join(problems))

    finding_ids: list[int] = []
    with db.tx(conn):
        confirmations: list[int] = []
        confirmed_groups: list[tuple[str, int, dict[str, Any], list[int], dict[str, Any]]] = []
        for (kind, idx), items in groups.items():
            opt = group_opts.get((kind, idx), {})
            if not opt.get("include", True):
                for it in items:
                    confirmations.append(db.confirm(conn, actor, "extracted_items", it["id"], "reject",
                                                    before={"value": it["value"]}))
                continue
            values: dict[str, Any] = {}
            src: set[int] = set()
            for it in items:
                upd = item_updates.get(it["id"], {})
                new_value = upd.get("value", it["caregiver_value"])
                new_value = new_value.strip() if isinstance(new_value, str) else new_value
                new_value = new_value or None
                action = "edit" if new_value != it["value"] else "confirm"
                conn.execute("UPDATE extracted_items SET caregiver_value=?, confirmed=1 WHERE id=?",
                             (new_value, it["id"]))
                confirmations.append(db.confirm(conn, actor, "extracted_items", it["id"], action,
                                                before={"value": it["value"]}, after={"value": new_value}))
                values[it["field"]] = new_value
                src.update(json.loads(it["source_line_ids"]))
            confirmed_groups.append((kind, idx, values, sorted(src), opt))
        conn.execute("UPDATE extractions SET status='confirmed' WHERE id=?", (ext_id,))

        # Non-medication facts go straight to memory as confirmed timeline events.
        for kind, idx, values, src, _opt in confirmed_groups:
            if kind == "allergy" and values.get("substance"):
                db.event(conn, "allergy", f"Allergy: {values['substance']}"
                         + (f" ({values['reaction']})" if values.get("reaction") else ""),
                         person_id=db.parent_id(conn), ref_table="extractions", ref_id=ext_id)
            elif kind == "follow_up" and values.get("what"):
                db.event(conn, "follow_up", f"Follow-up: {values['what']}"
                         + (f" — {values['when']}" if values.get("when") else ""),
                         person_id=db.parent_id(conn), ref_table="extractions", ref_id=ext_id)
            elif kind == "instruction" and values.get("text"):
                db.event(conn, "instruction", values["text"], person_id=db.parent_id(conn),
                         ref_table="extractions", ref_id=ext_id)

        finding_ids = _reconcile(conn, ext_id, [g for g in confirmed_groups if g[0] == "medication"])
        db.event(conn, "extraction_confirmed",
                 f"Caregiver reviewed document #{ext['document_id']}; {len(finding_ids)} items to reconcile",
                 person_id=actor, ref_table="extractions", ref_id=ext_id)
    _write_notes(conn, finding_ids)
    return finding_ids


def _reconcile(conn: sqlite3.Connection, ext_id: int,
               meds: list[tuple[str, int, dict[str, Any], list[int], dict[str, Any]]]) -> list[int]:
    active = careplan.active_meds(conn)
    matched: set[int] = set()
    ids: list[int] = []

    def add(kind: str, med_id: int | None, idx: int | None, src: list[int], detail: dict[str, Any],
            decision: str | None = None) -> None:
        cur = conn.execute(
            "INSERT INTO reconciliation_findings (extraction_id, medication_id, group_index, kind, source_line_ids, "
            "detail_json, decision, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (ext_id, med_id, idx, kind, json.dumps(src), json.dumps(detail), decision, db.iso()))
        ids.append(int(cur.lastrowid))

    for _kind, idx, v, src, opt in meds:
        name = v.get("name") or ""
        ingredient = (opt.get("ingredient") or "").strip().lower() or None
        rxcui = opt.get("rxcui")
        ingredient_source = opt.get("ingredient_source") or ("caregiver" if ingredient else None)
        if not ingredient:
            norm = drugs.normalize(name)
            if norm.status == "exact" and norm.ingredient:
                ingredient, rxcui, ingredient_source = norm.ingredient, norm.rxcui, "rxnorm"
        times = opt.get("times")
        if times is None:
            times = careplan.times_from_frequency(v.get("frequency"), v.get("timing"))
        proposed = {**v, "ingredient": ingredient, "rxcui": rxcui, "ingredient_source": ingredient_source,
                    "times": careplan.valid_times(times)}
        key = _key(ingredient) or _key(drugs.clean_name(name))
        match = None
        for m in active:
            mkeys = {_key(m["ingredient"]), _key(drugs.clean_name(m["name_as_written"]))} - {""}
            if key and key in mkeys:
                match = m
                break
        action = v.get("action") or "start"
        if action == "stop":
            if match:
                matched.add(match["id"])
                add("explicitly_stopped", match["id"], idx, src, {"proposed": proposed, "current": match})
            else:
                add("explicitly_stopped", None, idx, src, {"proposed": proposed, "current": None})
            continue
        if not match:
            add("new", None, idx, src, {"proposed": proposed})
            continue
        matched.add(match["id"])
        same_name = _same(drugs.clean_name(name), drugs.clean_name(match["name_as_written"]))
        if action == "start" and not same_name:
            add("possible_duplicate", match["id"], idx, src, {"proposed": proposed, "current": match})
            continue
        diffs = [f for f in ("strength", "dose", "frequency") if v.get(f) and not _same(v.get(f), match.get(f))]
        if diffs:
            add("changed", match["id"], idx, src, {"proposed": proposed, "current": match, "fields": diffs})
        else:
            add("unchanged", match["id"], idx, src, {"proposed": proposed, "current": match}, decision="no_change")

    for m in active:
        if m["id"] not in matched:
            add("not_mentioned", m["id"], None, [], {"current": m})
    return ids


NOTES_SYSTEM = """You write one short, plain-English line per medication-list finding for a family caregiver.
Say what the document shows compared with the current list. Never advise starting, stopping or changing a
medicine, never judge safety, and never add facts that are not in the finding. For "not_mentioned", say the
document does not mention the medicine and the caregiver should confirm with the doctor whether to continue.
Return JSON: {"notes": [{"id": <finding id>, "note": "..."}]}"""


def _fallback_note(f: dict[str, Any]) -> str:
    d = json.loads(f["detail_json"])
    p, c = d.get("proposed") or {}, d.get("current") or {}
    kind = f["kind"]
    if kind == "new":
        return f"New in this document: {p.get('name')} {p.get('strength') or ''}".strip()
    if kind == "changed":
        return f"{c.get('name_as_written')}: document differs in {', '.join(d.get('fields', []))}"
    if kind == "explicitly_stopped":
        return f"Document says to stop {p.get('name')}"
    if kind == "possible_duplicate":
        return f"{p.get('name')} may be the same medicine as {c.get('name_as_written')} ({p.get('ingredient')})"
    if kind == "not_mentioned":
        return f"{c.get('name_as_written')} is not mentioned in this document. Still taking it?"
    return f"{p.get('name')}: no change"


def _write_notes(conn: sqlite3.Connection, finding_ids: list[int]) -> None:
    if not finding_ids:
        return
    q = ",".join("?" * len(finding_ids))
    findings = db.rows(conn.execute(f"SELECT * FROM reconciliation_findings WHERE id IN ({q})", finding_ids))
    for f in findings:
        conn.execute("UPDATE reconciliation_findings SET note=? WHERE id=?", (_fallback_note(f), f["id"]))
    compact = []
    for f in findings:
        d = json.loads(f["detail_json"])
        p, c = d.get("proposed") or {}, d.get("current") or {}
        compact.append({"id": f["id"], "kind": f["kind"],
                        "document": {k: p.get(k) for k in ("name", "strength", "dose", "frequency", "action")} if p else None,
                        "current_list": {k: c.get(k) for k in ("name_as_written", "strength", "dose", "frequency")} if c else None})
    try:
        data, _ = llm.chat_json(conn, "reconciliation_notes", settings.model_fast, NOTES_SYSTEM,
                                json.dumps(compact, ensure_ascii=False), max_tokens=6000)
    except (llm.LLMError, ValueError):
        return  # template notes stay in place
    valid = set(finding_ids)
    for n in data.get("notes", []) if isinstance(data, dict) else []:
        try:
            fid = int(n.get("id"))
        except (TypeError, ValueError):
            continue
        note = str(n.get("note") or "").strip()
        if fid in valid and note:
            conn.execute("UPDATE reconciliation_findings SET note=? WHERE id=?", (note[:300], fid))


def decide(conn: sqlite3.Connection, finding_id: int, decision: str) -> dict[str, Any]:
    """Apply one caregiver decision. Returns {"new_medication_ids": [...]} for follow-up label review."""
    f = db.one(conn.execute("SELECT * FROM reconciliation_findings WHERE id=?", (finding_id,)))
    if not f:
        raise LookupError("finding not found")
    if f["decision"]:
        raise ConfirmationError("This item was already decided")
    if decision not in DECISIONS[f["kind"]]:
        raise ConfirmationError(f"'{decision}' is not a valid choice for {f['kind']}")
    if f["kind"] == "explicitly_stopped" and not f["medication_id"] and decision != "acknowledge":
        raise ConfirmationError("Nothing to stop: this medicine is not on the current list")
    actor = db.caregiver_id(conn)
    d = json.loads(f["detail_json"])
    p = d.get("proposed") or {}
    new_ids: list[int] = []
    with db.tx(conn):
        before = careplan.snapshot(conn)
        med_id = f["medication_id"]
        if f["kind"] == "new" and decision == "accept":
            new_ids.append(careplan.add_medication(conn, p, source_item_id=None, confirmation_id=None,
                                                   times=p.get("times") or []))
        elif f["kind"] == "changed" and decision == "accept":
            conn.execute("UPDATE medications SET name_as_written=COALESCE(?, name_as_written), strength=?, dose=?, "
                         "frequency=?, instructions=?, updated_at=? WHERE id=?",
                         (p.get("name"), p.get("strength"), p.get("dose"), p.get("frequency"), p.get("instructions"),
                          db.iso(), med_id))
            careplan.set_schedule(conn, med_id, p.get("times") or [])
        elif f["kind"] in ("explicitly_stopped", "not_mentioned") and decision == "stop":
            careplan.stop_medication(conn, med_id)
        elif f["kind"] == "not_mentioned" and decision == "ask_doctor":
            conn.execute("UPDATE medications SET ask_doctor=1, updated_at=? WHERE id=?", (db.iso(), med_id))
        elif f["kind"] == "possible_duplicate" and decision == "replace":
            careplan.stop_medication(conn, med_id)
            new_ids.append(careplan.add_medication(conn, p, source_item_id=None, confirmation_id=None,
                                                   times=p.get("times") or []))
        elif f["kind"] == "possible_duplicate" and decision == "keep_both":
            new_ids.append(careplan.add_medication(conn, p, source_item_id=None, confirmation_id=None,
                                                   times=p.get("times") or []))
        cid = db.confirm(conn, actor, "reconciliation_findings", finding_id, decision,
                         before=before, after=None)
        for nid in new_ids:
            conn.execute("UPDATE medications SET confirmation_id=? WHERE id=?", (cid, nid))
        if med_id and decision in ("accept", "stop", "replace", "ask_doctor"):
            conn.execute("UPDATE medications SET confirmation_id=? WHERE id=?", (cid, med_id))
        conn.execute("UPDATE reconciliation_findings SET decision=?, confirmation_id=? WHERE id=?",
                     (decision, cid, finding_id))
        changed_plan = careplan.snapshot(conn) != before
        if changed_plan:
            careplan.new_version(conn, f"{f['kind']} → {decision}: {f['note'] or ''}"[:200], [cid])
        conn.execute("UPDATE confirmations SET after_json=? WHERE id=?",
                     (json.dumps(careplan.snapshot(conn)), cid))
        db.event(conn, "plan_decision", f"{(f['note'] or f['kind'])} → {decision.replace('_', ' ')}",
                 person_id=actor, ref_table="reconciliation_findings", ref_id=finding_id)
    return {"new_medication_ids": new_ids}
