"""Nebius Token Factory client. Every call is logged to model_runs with the exact (redacted) prompt."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Protocol

from . import db
from .config import price_for, settings


class LLMError(RuntimeError):
    pass


class RateLimited(LLMError):
    pass


@dataclass
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int


class Backend(Protocol):
    def __call__(self, model: str, messages: list[dict[str, Any]], max_tokens: int,
                 temperature: float) -> Completion: ...


def _openai_backend(model: str, messages: list[dict[str, Any]], max_tokens: int,
                    temperature: float) -> Completion:
    if not settings.nebius_api_key:
        raise LLMError("NEBIUS_API_KEY is not set")
    from openai import OpenAI

    client = OpenAI(base_url=settings.nebius_base_url, api_key=settings.nebius_api_key, timeout=120)
    resp = client.chat.completions.create(
        model=model, messages=messages, max_tokens=max_tokens, temperature=temperature
    )
    msg = resp.choices[0].message
    text = msg.content or ""
    usage = resp.usage
    return Completion(
        text=text,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
    )


_backend: Backend | Callable[..., Completion] = _openai_backend


def set_backend(fn: Backend | Callable[..., Completion] | None) -> None:
    """Swap the transport (used by tests). None restores the real Token Factory client."""
    global _backend
    _backend = fn or _openai_backend


def _prompt_record(messages: list[dict[str, Any]]) -> str:
    parts = []
    for m in messages:
        content = m["content"]
        if isinstance(content, list):  # multimodal: never log image bytes
            content = " ".join(
                c.get("text", "[image: identifier regions masked]") if isinstance(c, dict) else str(c)
                for c in content
            )
        parts.append(f"[{m['role']}]\n{content}")
    return "\n\n".join(parts)


def check_rate(conn: sqlite3.Connection) -> None:
    since = db.iso(db.now() - timedelta(hours=1))
    n = conn.execute("SELECT COUNT(*) FROM model_runs WHERE created_at > ?", (since,)).fetchone()[0]
    if n >= settings.model_calls_per_hour:
        raise RateLimited(f"Hourly model-call limit ({settings.model_calls_per_hour}) reached for this workspace")


def chat(conn: sqlite3.Connection, task: str, model: str, messages: list[dict[str, Any]], *,
         max_tokens: int = 2048, temperature: float = 0.1) -> tuple[str, int]:
    """Run one chat completion and log it. Returns (text, model_run_id)."""
    check_rate(conn)
    started = time.monotonic()
    prompt = _prompt_record(messages)
    try:
        out = _backend(model, messages, max_tokens, temperature)
    except Exception as exc:  # log the failure, then re-raise
        latency = int((time.monotonic() - started) * 1000)
        conn.execute(
            "INSERT INTO model_runs (task, model_id, redacted_prompt, latency_ms, ok, error, created_at) "
            "VALUES (?,?,?,?,0,?,?)",
            (task, model, prompt, latency, f"{type(exc).__name__}: {exc}"[:500], db.iso()),
        )
        raise LLMError(f"{task} failed on {model}: {exc}") from exc
    latency = int((time.monotonic() - started) * 1000)
    pin, pout = price_for(model)
    cost = out.prompt_tokens / 1e6 * pin + out.completion_tokens / 1e6 * pout
    cur = conn.execute(
        "INSERT INTO model_runs (task, model_id, redacted_prompt, response_text, prompt_tokens, completion_tokens, "
        "latency_ms, cost_usd, ok, created_at) VALUES (?,?,?,?,?,?,?,?,1,?)",
        (task, model, prompt, out.text, out.prompt_tokens, out.completion_tokens, latency, round(cost, 6), db.iso()),
    )
    return out.text, int(cur.lastrowid)


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def parse_json(text: str) -> Any:
    """Parse the first JSON object in a model reply, tolerating reasoning blocks and code fences."""
    cleaned = _THINK.sub("", text)
    if "</think>" in cleaned:  # reply that opened reasoning without a tag
        cleaned = cleaned.split("</think>", 1)[1]
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1)
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(cleaned[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : i + 1])
    raise ValueError("unterminated JSON object in reply")


def chat_json(conn: sqlite3.Connection, task: str, model: str, system: str, user: str, *,
              max_tokens: int = 4096) -> tuple[Any, int]:
    """Ask for JSON; on a parse failure retry once with the error. Returns (data, model_run_id)."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    text, run_id = chat(conn, task, model, messages, max_tokens=max_tokens)
    try:
        return parse_json(text), run_id
    except (ValueError, json.JSONDecodeError) as exc:
        messages += [
            {"role": "assistant", "content": text[:4000]},
            {"role": "user", "content": f"That was not valid JSON ({exc}). Reply with only the JSON object."},
        ]
        text, run_id = chat(conn, task + ":retry", model, messages, max_tokens=max_tokens)
        try:
            return parse_json(text), run_id
        except (ValueError, json.JSONDecodeError) as exc2:
            raise LLMError(f"{task}: model did not return valid JSON ({exc2})") from exc2
