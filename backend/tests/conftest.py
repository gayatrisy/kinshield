"""Test fixtures: fake Token Factory, RxNorm/openFDA and Telegram transports. No network is used."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="kinshield-test-")
os.environ["KINSHIELD_DATA_DIR"] = _TMP
os.environ["KINSHIELD_SECRET"] = "test-secret"
os.environ["TELEGRAM_BOT_TOKEN"] = "TEST"
os.environ["TELEGRAM_DEMO_CHAT_ID"] = "-1001"
os.environ["KINSHIELD_DEMO_MODE"] = "1"

import pytest  # noqa: E402

from kinshield import drugs, llm, notify  # noqa: E402

INGREDIENTS = {"clopidogrel": "11289", "metoprolol succinate": "221124", "metoprolol": "6918", "amlodipine": "17767",
               "atorvastatin": "83367", "aspirin": "1191", "omeprazole": "7646", "ibuprofen": "5640",
               "metformin": "6809", "paracetamol": "161"}
LABELS = {
    "CLOPIDOGREL": {"set_id": "clop-set", "warnings_and_cautions": [
        "5.1 Diminished antiplatelet activity. Avoid concomitant use of omeprazole or esomeprazole with clopidogrel. "
        "5.2 General risk of bleeding. Concomitant use with aspirin, NSAIDs or warfarin increases the risk of bleeding."]},
    "METFORMIN": {"set_id": "met-set", "warnings": ["Lactic acidosis has been reported."]},
}


def fake_http(url: str, params: dict) -> dict | None:
    if "rxcui.json" in url:
        name = params["name"]
        key = next((k for k in INGREDIENTS if k == name or name.startswith(k)), None)
        return {"idGroup": {"rxnormId": [INGREDIENTS[key]] if key else []}}
    if "approximateTerm" in url:
        return {"approximateGroup": {"candidate": []}}
    if "/related.json" in url:
        cui = url.split("/rxcui/")[1].split("/")[0]
        name = next((k for k, v in INGREDIENTS.items() if v == cui), None)
        return {"relatedGroup": {"conceptGroup": [{"conceptProperties": [{"rxcui": cui, "name": name.split()[0]}]}]}}
    if "properties.json" in url:
        return {}
    if "api.fda.gov" in url:
        m = re.search(r'"(.+)"', params["search"])
        lab = LABELS.get(m.group(1)) if m else None
        return {"results": [lab]} if lab else None
    raise AssertionError(url)


def _extract(user: str) -> dict:
    meds, allergies, follow, other = [], [], [], []
    for line in user.splitlines():
        m = re.match(r"(L\d+): (.*)", line)
        if not m:
            continue
        lid, text = m.groups()
        med = re.search(r"(?:Tab|Cap)\s+([A-Z][a-zA-Z]+(?:\s+Succinate)?)\s+(\d+\s*mg)\s+(\d+\s+\w+)\s+([\d\-]+)", text)
        if re.search(r"\bSTOP\b", text):
            name = re.search(r"STOP\s+(?:Tab\s+)?(\w+)", text).group(1)
            meds.append({"name": name, "strength": None, "form": "tablet", "dose": None, "frequency": None,
                         "timing": None, "duration": None, "instructions": "Avoid all NSAID painkillers",
                         "action": "stop", "source_lines": [lid]})
        elif med:
            name, strength, dose, freq = med.groups()
            action = "start" if "new" in text else "change" if "increased" in text else "continue"
            meds.append({"name": name, "strength": strength, "form": "tablet", "dose": dose, "frequency": freq,
                         "timing": None, "duration": None, "instructions": None, "action": action,
                         "source_lines": [lid]})
        elif text.startswith("Sulfa"):
            allergies.append({"substance": "Sulfa drugs", "reaction": "skin rash", "source_lines": [lid]})
        elif "OPD review" in text:
            follow.append({"what": "Cardiology OPD review", "when": "after 2 weeks", "source_lines": [lid]})
    return {"medications": meds, "allergies": allergies, "follow_ups": follow, "other_instructions": other}


def fake_llm(model: str, messages: list, max_tokens: int, temperature: float) -> llm.Completion:
    system = messages[0]["content"] if isinstance(messages[0]["content"], str) else ""
    user = messages[-1]["content"] if isinstance(messages[-1]["content"], str) else ""
    if "discharge summaries" in system:
        out = _extract(user)
    elif "medication-list finding" in system:
        out = {"notes": [{"id": f["id"], "note": f"Note for {f['kind']}"} for f in json.loads(user)]}
    elif "drug label" in system:
        out = {"explanation": "The label says to avoid using these together.", "question": "Should we change anything?"}
    elif "pre-visit brief" in system or "KinShield, a private assistant" in system:
        recs = json.loads(user.split("Records:\n", 1)[1].split("\n\nCaregiver's question", 1)[0])
        med = next(k for k, v in recs.items() if v["type"] == "medication")
        items = [{"text": "Cited statement.", "cites": [med]}, {"text": "Bogus statement.", "cites": ["med:9999"]}]
        out = {"summary": items, "questions": items, "answer": items}
    else:
        raise AssertionError("unexpected prompt: " + system[:80])
    return llm.Completion(text="<think>reasoning</think>" + json.dumps(out), prompt_tokens=1000, completion_tokens=200)


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.updates: list[dict] = []
        self.fail = False

    def __call__(self, method: str, payload: dict) -> dict:
        if method == "sendMessage":
            if self.fail:
                return {"ok": False, "description": "Forbidden: bot was blocked by the user"}
            self.sent.append(payload)
            return {"ok": True, "result": {"message_id": len(self.sent)}}
        if method == "getUpdates":
            ups, self.updates = self.updates, []
            return {"ok": True, "result": ups}
        return {"ok": True}


@pytest.fixture(autouse=True)
def fakes():
    tg = FakeTelegram()
    llm.set_backend(fake_llm)
    drugs.set_http(fake_http)
    notify.set_transport(tg)
    yield tg
    llm.set_backend(None)
    drugs.set_http(None)
    notify.set_transport(None)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from kinshield.api import app

    c = TestClient(app)
    r = c.post("/api/session/demo")
    assert r.status_code == 200
    return c


DATA_DIR = Path(_TMP)
