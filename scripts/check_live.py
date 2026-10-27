"""Day-1 live checks: run on your own machine or the server, with .env filled in.

    python scripts/check_live.py            # all checks
    python scripts/check_live.py --telegram # also send one test message

Prints PASS/FAIL per check and never prints your keys.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

import httpx  # noqa: E402

from kinshield.config import settings  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str):
    def wrap(fn):
        started = time.monotonic()
        try:
            detail = fn() or ""
            results.append((name, True, f"{detail} ({time.monotonic() - started:.1f}s)"))
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, f"{type(exc).__name__}: {exc}"[:400]))
        return fn
    return wrap


def client():
    from openai import OpenAI
    return OpenAI(base_url=settings.nebius_base_url, api_key=settings.nebius_api_key, timeout=120)


@check("Nebius key is set")
def _key():
    assert settings.nebius_api_key, "NEBIUS_API_KEY is empty in .env"
    return "set"


@check("Token Factory /v1/models lists our models")
def _models():
    ids = sorted(m.id for m in client().models.list().data)
    nemotron = [i for i in ids if "nemotron" in i.lower()]
    print("\nNemotron models on your account:\n  " + "\n  ".join(nemotron or ["(none)"]))
    wanted = {"KS_MODEL_FAST": settings.model_fast, "KS_MODEL_MAIN": settings.model_main,
              "KS_MODEL_DEEP": settings.model_deep, "KS_MODEL_VISION": settings.model_vision}
    missing = [f"{k}={v}" for k, v in wanted.items() if v not in ids]
    assert not missing, "not in catalog, fix .env: " + ", ".join(missing)
    return f"{len(nemotron)} Nemotron models; all four configured IDs found"


for label, model in (("fast", settings.model_fast), ("main", settings.model_main), ("deep", settings.model_deep)):
    @check(f"JSON reply from {label} model ({model})")
    def _json(model=model):
        from kinshield.llm import parse_json
        r = client().chat.completions.create(
            model=model, max_tokens=400, temperature=0,
            messages=[{"role": "system", "content": "Reply with only a JSON object."},
                      {"role": "user", "content": 'Return {"drug": "metformin", "strength": "500 mg"} exactly.'}])
        data = parse_json(r.choices[0].message.content or "")
        assert data.get("drug") == "metformin", data
        u = r.usage
        return f"ok, {u.prompt_tokens}+{u.completion_tokens} tokens"


@check("Vision model reads an image")
def _vision():
    import base64
    from kinshield import demo
    files = demo.ensure_demo_files(ROOT / "demo-data" / ".cache")
    b64 = base64.b64encode(files["photo"].read_bytes()).decode()
    r = client().chat.completions.create(
        model=settings.model_vision, max_tokens=300,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "What is the first medicine listed under 'Medicines on discharge'? One word."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}])
    text = r.choices[0].message.content or ""
    assert "clopidogrel" in text.lower(), text[:200]
    return "read Clopidogrel from the photo"


@check("Local OCR (Tesseract) works")
def _ocr():
    from kinshield import demo, ocr
    files = demo.ensure_demo_files(ROOT / "demo-data" / ".cache")
    res = ocr.read_document(files["photo"], ROOT / "demo-data" / ".cache" / "ocr")
    assert any("Clopidogrel" in l.text for l in res.lines), "Clopidogrel not found in OCR output"
    return f"{len(res.lines)} lines"


@check("RxNorm reachable")
def _rx():
    from kinshield import drugs
    n = drugs.normalize("Atorvastatin 40 mg")
    assert n.status == "exact" and n.ingredient, n
    return f"atorvastatin → rxcui {n.rxcui}"


@check("openFDA label reachable")
def _fda():
    from kinshield import drugs
    lab = drugs.fetch_label("clopidogrel")
    assert lab and lab.sections, "no label"
    hits = drugs.find_mentions(lab, "omeprazole")
    return f"label found; omeprazole mentioned in {len(hits)} passage(s)"


@check("Telegram bot token valid")
def _tg():
    assert settings.telegram_token, "TELEGRAM_BOT_TOKEN is empty"
    r = httpx.get(f"https://api.telegram.org/bot{settings.telegram_token}/getMe", timeout=20).json()
    assert r.get("ok"), r
    user = r["result"]["username"]
    if settings.telegram_bot_username and settings.telegram_bot_username != user:
        raise AssertionError(f"TELEGRAM_BOT_USERNAME should be {user}")
    return f"@{user}"


if "--telegram" in sys.argv:
    @check("Telegram test message to TELEGRAM_DEMO_CHAT_ID")
    def _tgsend():
        assert settings.telegram_demo_chat_id, "TELEGRAM_DEMO_CHAT_ID is empty"
        r = httpx.post(f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage",
                       json={"chat_id": settings.telegram_demo_chat_id, "text": "KinShield test message"}, timeout=20).json()
        assert r.get("ok"), r.get("description")
        return "accepted by Telegram (check your phone)"


print()
width = max(len(n) for n, _, _ in results)
for name, ok, detail in results:
    print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
failed = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
