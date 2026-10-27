"""Telegram notifications. 'accepted' means Telegram's API took the message, not that anyone read it."""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from typing import Any, Callable

import httpx

from . import db
from .config import settings

API = "https://api.telegram.org/bot{token}/{method}"


def _tg_call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    r = httpx.post(API.format(token=settings.telegram_token, method=method), json=payload, timeout=30)
    try:
        data = r.json()
    except ValueError:
        data = {"ok": False, "description": f"HTTP {r.status_code}"}
    return data


_call: Callable[[str, dict[str, Any]], dict[str, Any]] = _tg_call


def set_transport(fn: Callable[[str, dict[str, Any]], dict[str, Any]] | None) -> None:
    global _call
    _call = fn or _tg_call


def _sig(ws_id: str, notif_id: int) -> str:
    return hmac.new(settings.secret_key.encode(), f"{ws_id}:{notif_id}".encode(), hashlib.sha256).hexdigest()[:10]


def seen_callback(ws_id: str, notif_id: int) -> str:
    return f"s:{ws_id}:{notif_id}:{_sig(ws_id, notif_id)}"  # < 64 bytes


def parse_seen_callback(data: str) -> tuple[str, int] | None:
    try:
        tag, ws_id, nid, sig = data.split(":")
        n = int(nid)
    except ValueError:
        return None
    if tag != "s" or not hmac.compare_digest(sig, _sig(ws_id, n)):
        return None
    return ws_id, n


def caregiver_chat(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT telegram_chat_id FROM people WHERE role='caregiver' ORDER BY id LIMIT 1").fetchone()
    if row and row["telegram_chat_id"]:
        return str(row["telegram_chat_id"])
    return settings.telegram_demo_chat_id or None


def send(conn: sqlite3.Connection, ws_id: str, reason: str, body: str, *, acknowledgement_id: int | None = None,
         resend_of: int | None = None) -> int:
    """Send to the caregiver with a 'Seen' button. Always records the outcome honestly."""
    person = conn.execute("SELECT id FROM people WHERE role='caregiver' ORDER BY id LIMIT 1").fetchone()
    person_id = person["id"] if person else None
    cur = conn.execute(
        "INSERT INTO notifications (person_id, channel, reason, body, acknowledgement_id, api_status, sent_at, resend_of) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (person_id, "telegram", reason, body, acknowledgement_id, "not_configured", db.iso(), resend_of))
    nid = int(cur.lastrowid)
    chat = caregiver_chat(conn)
    if not settings.telegram_token or not chat:
        conn.execute("UPDATE notifications SET error=? WHERE id=?",
                     ("Telegram is not configured for this workspace", nid))
        return nid
    payload = {
        "chat_id": chat,
        "text": body,
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": [[{"text": "Seen ✓", "callback_data": seen_callback(ws_id, nid)}]]},
    }
    try:
        resp = _call("sendMessage", payload)
    except httpx.HTTPError as exc:
        resp = {"ok": False, "description": str(exc)}
    if resp.get("ok"):
        mid = str((resp.get("result") or {}).get("message_id", ""))
        conn.execute("UPDATE notifications SET api_status='accepted', telegram_message_id=? WHERE id=?", (mid, nid))
    else:
        conn.execute("UPDATE notifications SET api_status='failed', error=? WHERE id=?",
                     (str(resp.get("description") or "unknown error")[:300], nid))
    return nid


def mark_seen(conn: sqlite3.Connection, notif_id: int) -> bool:
    cur = conn.execute("UPDATE notifications SET seen_at=? WHERE id=? AND seen_at IS NULL", (db.iso(), notif_id))
    if cur.rowcount:
        db.event(conn, "alert_seen", f"Caregiver tapped Seen on alert #{notif_id}",
                 ref_table="notifications", ref_id=notif_id)
        # a resend and its original are the same alert
        conn.execute("UPDATE notifications SET seen_at=? WHERE (id=(SELECT resend_of FROM notifications WHERE id=?) "
                     "OR resend_of=?) AND seen_at IS NULL", (db.iso(), notif_id, notif_id))
    return bool(cur.rowcount)


def poll_updates(open_ws: Callable[[str], sqlite3.Connection]) -> int:
    """Process Telegram updates: 'Seen' taps and '/start <code>' chat links. Returns updates handled."""
    if not settings.telegram_token:
        return 0
    offset = int(db.kv_get("tg_offset") or 0)
    try:
        resp = _call("getUpdates", {"offset": offset, "timeout": 0,
                                    "allowed_updates": ["message", "callback_query", "channel_post"]})
    except httpx.HTTPError:
        return 0
    if not resp.get("ok"):
        return 0
    handled = 0
    for upd in resp.get("result", []):
        offset = max(offset, int(upd["update_id"]) + 1)
        cq = upd.get("callback_query")
        if cq:
            parsed = parse_seen_callback(cq.get("data", ""))
            text = "Could not record that."
            if parsed:
                ws_id, nid = parsed
                try:
                    conn = open_ws(ws_id)
                    try:
                        text = "Marked as seen." if mark_seen(conn, nid) else "Already marked as seen."
                    finally:
                        conn.close()
                except Exception:  # workspace expired or removed
                    text = "This demo workspace has expired."
            try:
                _call("answerCallbackQuery", {"callback_query_id": cq["id"], "text": text})
            except httpx.HTTPError:
                pass
            handled += 1
            continue
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        if text.startswith("/start "):
            code = text.split(" ", 1)[1].strip()
            linked = _link_chat(open_ws, code, str(msg.get("chat", {}).get("id")))
            try:
                _call("sendMessage", {"chat_id": msg["chat"]["id"],
                                      "text": "KinShield alerts will come to this chat." if linked
                                      else "That link code was not recognised or has expired."})
            except httpx.HTTPError:
                pass
            handled += 1
    db.kv_set("tg_offset", str(offset))
    return handled


def _link_chat(open_ws: Callable[[str], sqlite3.Connection], code: str, chat_id: str) -> bool:
    try:
        ws_id, _rest = code.split("_", 1)
    except ValueError:
        return False
    if ws_id not in db.live_workspaces():
        return False
    conn = open_ws(ws_id)
    try:
        cur = conn.execute("UPDATE people SET telegram_chat_id=? WHERE role='caregiver' AND telegram_link_code=?",
                           (chat_id, code))
        if cur.rowcount:
            db.event(conn, "telegram_linked", "Caregiver linked a Telegram chat for alerts")
        return bool(cur.rowcount)
    finally:
        conn.close()


def link_url(code: str) -> str | None:
    if not settings.telegram_bot_username:
        return None
    return f"https://t.me/{settings.telegram_bot_username}?start={code}"


def describe(n: dict[str, Any]) -> str:
    if n["api_status"] == "accepted":
        return "Seen by caregiver" if n.get("seen_at") else "Sent to Telegram, not yet seen"
    if n["api_status"] == "failed":
        return "Failed to send"
    return "Not sent: Telegram not configured"


__all__ = ["send", "mark_seen", "poll_updates", "describe", "link_url", "json"]
