"""Pattern-based identifier redaction, applied before any text is sent to a cloud model.

This is a best-effort filter, not a guarantee: it can miss identifiers it has no pattern for.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# "Label: value" lines whose value is an identifier. The label stays so the model keeps the structure.
_LABELLED = [
    (re.compile(r"(?i)(?:^|(?<=\s{2}))\s*(patient(?:'s)?\s*name|name\s*of\s*patient|pt\.?\s*name|name)\s*[:\-]\s*"
                r"(.+?)(?=\s{2,}|\s+[A-Z][A-Za-z/ .]{1,15}:|$)"), "[PATIENT]"),
    (re.compile(r"(?i)\b(d\.?\s*o\.?\s*b\.?|date\s*of\s*birth)\s*[:\-]?\s*(\S.*?)(?=\s{2,}|\s+[A-Z][A-Za-z/ .]{1,15}:|$)"), "[DOB]"),
    (re.compile(r"(?i)\b(address|addr\.?|residence)\s*[:\-]\s*(.+)"), "[ADDRESS]"),
    (re.compile(r"(?i)\b(uhid|mrn|ip\s*no\.?|op\s*no\.?|reg(?:istration)?\.?\s*no\.?|hospital\s*no\.?|patient\s*id|"
                r"aadhaa?r(?:\s*no\.?)?|abha(?:\s*(?:no\.?|id))?|policy\s*no\.?|id\s*no\.?)\s*[:\-#]?\s*"
                r"(\S.*?)(?=\s{2,}|\s+[A-Z][A-Za-z/ .]{1,15}:|$)"), "[ID]"),
    (re.compile(r"(?i)\b(phone|mobile|mob\.?|tel\.?|contact\s*no\.?)\s*[:\-]?\s*(\+?\d[\d\s-]{6,}\d)"), "[PHONE]"),
    (re.compile(r"(?i)\b(attendant|guardian|next\s*of\s*kin|emergency\s*contact|relative)\s*[:\-]\s*"
                r"(.+?)(?=\s{2,}|\s+[A-Z][A-Za-z/ .]{1,15}:|$)"), "[CONTACT]"),
]

_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"), "[PHONE]"),
    (re.compile(r"(?<!\d)\+?\d{1,3}[\s-]?\(?\d{2,4}\)?[\s-]\d{3,4}[\s-]\d{3,4}(?!\d)"), "[PHONE]"),
    (re.compile(r"(?<!\d)\d{4}\s\d{4}\s\d{4}(?!\d)"), "[ID]"),  # Aadhaar-style
    (re.compile(r"\b(?:Dr|Dr\.|Doctor)\s+[A-Z][a-zA-Z.]*(?:\s+[A-Z][a-zA-Z.]*){0,2}"), "[CLINICIAN]"),
    (re.compile(r"\b(?:Mr|Mrs|Ms|Shri|Smt)\.?\s+[A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*){0,2}"), "[PERSON]"),
    (re.compile(r"(?i)\b\d{1,4}\s*,\s*[A-Za-z][\w .]{0,40}?\b(?:road|rd|street|nagar|colony|lane|marg|sector|"
                r"apartments?|society|chowk|peth)\b[^\n]{0,60}"), "[ADDRESS]"),
    (re.compile(r"(?<![\w.])\d{6}(?![\w.])"), "[PIN]"),
]

# Words that look like names to the patterns above but are clinical vocabulary.
_SAFE_WORDS = {"tablet", "tab", "capsule", "cap", "syrup", "injection"}


@dataclass
class Redactor:
    names: list[str]

    def _name_pattern(self) -> re.Pattern[str] | None:
        tokens: set[str] = set()
        for full in self.names:
            full = full.strip()
            if not full:
                continue
            tokens.add(full)
            for part in re.split(r"\s+", full):
                if len(part) >= 3 and part.lower() not in _SAFE_WORDS:
                    tokens.add(part)
        if not tokens:
            return None
        alts = sorted((re.escape(t) for t in tokens), key=len, reverse=True)
        return re.compile(r"(?i)\b(?:" + "|".join(alts) + r")\b")

    def redact(self, text: str) -> str:
        out = text
        for pat, ph in _LABELLED:
            out = pat.sub(lambda m, ph=ph: f"{m.group(1)}: {ph}", out)
        name_pat = self._name_pattern()
        if name_pat:
            out = name_pat.sub("[PATIENT]", out)
        for pat, ph in _PATTERNS:
            out = pat.sub(ph, out)
        return out

    def changed(self, text: str) -> bool:
        return self.redact(text) != text
