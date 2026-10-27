"""RxNorm name normalization and openFDA label lookups. Matching between labels and medications is done in code."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

import httpx

RXNAV = "https://rxnav.nlm.nih.gov/REST"
OPENFDA = "https://api.fda.gov/drug/label.json"
LABEL_SECTIONS = ["boxed_warning", "contraindications", "warnings_and_cautions", "warnings",
                  "drug_interactions", "precautions"]


class LookupFailed(RuntimeError):
    pass


def _http_get(url: str, params: dict[str, Any]) -> dict[str, Any] | None:
    try:
        r = httpx.get(url, params=params, timeout=20)
    except httpx.HTTPError as exc:
        raise LookupFailed(str(exc)) from exc
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise LookupFailed(f"HTTP {r.status_code} from {url}")
    return r.json()


_get: Callable[[str, dict[str, Any]], dict[str, Any] | None] = _http_get


def set_http(fn: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None) -> None:
    global _get
    _get = fn or _http_get
    normalize.cache_clear()
    fetch_label.cache_clear()


_FORM_WORDS = r"(?i)\b(tab(let)?s?|cap(sule)?s?|syr(up)?|inj(ection)?|susp(ension)?|oral|sr|er|xl|cr|od|bd|tds|mg|mcg|g|ml|iu)\b\.?"


def clean_name(name: str) -> str:
    s = re.sub(r"\d+(\.\d+)?\s*(mg|mcg|g|ml|iu|%)?", " ", name, flags=re.I)
    s = re.sub(_FORM_WORDS, " ", s)
    s = re.sub(r"[^A-Za-z\- ]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


@dataclass(frozen=True)
class Normalized:
    query: str
    status: str  # 'exact' | 'approximate' | 'not_found' | 'lookup_failed'
    rxcui: str | None
    ingredient: str | None
    candidate_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"query": self.query, "status": self.status, "rxcui": self.rxcui,
                "ingredient": self.ingredient, "candidate_name": self.candidate_name}


def _ingredients_for(rxcui: str) -> tuple[str | None, str | None]:
    data = _get(f"{RXNAV}/rxcui/{rxcui}/related.json", {"tty": "IN+MIN"}) or {}
    for group in data.get("relatedGroup", {}).get("conceptGroup", []) or []:
        for c in group.get("conceptProperties", []) or []:
            return c.get("rxcui"), c.get("name", "").lower() or None
    props = _get(f"{RXNAV}/rxcui/{rxcui}/properties.json", {}) or {}
    p = props.get("properties") or {}
    if p.get("tty") in ("IN", "MIN"):
        return rxcui, (p.get("name") or "").lower() or None
    return None, None


@lru_cache(maxsize=512)
def normalize(name: str) -> Normalized:
    q = clean_name(name)
    if not q:
        return Normalized(name, "not_found", None, None)
    try:
        exact = _get(f"{RXNAV}/rxcui.json", {"name": q, "search": 2}) or {}
        ids = (exact.get("idGroup") or {}).get("rxnormId") or []
        if ids:
            in_cui, ingredient = _ingredients_for(ids[0])
            return Normalized(name, "exact", in_cui or ids[0], ingredient or q)
        approx = _get(f"{RXNAV}/approximateTerm.json", {"term": q, "maxEntries": 1}) or {}
        cands = (approx.get("approximateGroup") or {}).get("candidate") or []
        if cands:
            cui = cands[0].get("rxcui")
            in_cui, ingredient = _ingredients_for(cui) if cui else (None, None)
            return Normalized(name, "approximate", in_cui or cui, ingredient, cands[0].get("name") or ingredient)
        return Normalized(name, "not_found", None, None)
    except LookupFailed:
        return Normalized(name, "lookup_failed", None, None)


@dataclass
class Label:
    set_id: str | None
    sections: dict[str, str]
    url: str | None


@lru_cache(maxsize=256)
def fetch_label(ingredient: str) -> Label | None:
    """Return the first openFDA label for a generic name, or None when openFDA has none."""
    ing = ingredient.strip().upper().replace('"', "")
    data = _get(OPENFDA, {"search": f'openfda.generic_name:"{ing}"', "limit": 1})
    results = (data or {}).get("results") or []
    if not results:
        return None
    lab = results[0]
    sections = {}
    for key in LABEL_SECTIONS:
        val = lab.get(key)
        if val:
            sections[key] = " ".join(val) if isinstance(val, list) else str(val)
    set_id = lab.get("set_id")
    url = f"https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid={set_id}" if set_id else None
    return Label(set_id=set_id, sections=sections, url=url)


def find_mentions(label: Label, other: str, max_chars: int = 600) -> list[tuple[str, str]]:
    """Find sentences in the label that name `other`. Returns (section, passage) pairs."""
    if not other:
        return []
    words = [w for w in re.split(r"[\s/,]+", other.lower()) if len(w) >= 4]
    if not words:
        return []
    pat = re.compile(r"(?i)\b(" + "|".join(re.escape(w) for w in words) + r")\w*")
    hits: list[tuple[str, str]] = []
    for section, text in label.sections.items():
        sentences = re.split(r"(?<=[.;])\s+(?=[A-Z0-9(])", text)
        for i, s in enumerate(sentences):
            if pat.search(s):
                passage = s if len(s) <= max_chars else s[:max_chars] + "…"
                hits.append((section, passage.strip()))
                if len(hits) >= 3:
                    return hits
    return hits
