"""Synthetic demo data. Every person, number and document here is invented."""
from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from . import careplan, db

PARENT = "Ramesh Kulkarni"
CAREGIVER = "Anjali Kulkarni"

EXISTING_MEDS = [
    {"name": "Metformin", "ingredient": "metformin", "strength": "500 mg", "form": "tablet", "dose": "1 tablet",
     "frequency": "1-0-1", "instructions": "after food", "times": ["08:00", "21:00"]},
    {"name": "Amlodipine", "ingredient": "amlodipine", "strength": "5 mg", "form": "tablet", "dose": "1 tablet",
     "frequency": "1-0-0", "instructions": None, "times": ["08:00"]},
    {"name": "Atorvastatin", "ingredient": "atorvastatin", "strength": "20 mg", "form": "tablet",
     "dose": "1 tablet", "frequency": "0-0-1", "instructions": "at bedtime", "times": ["21:00"]},
    {"name": "Aspirin", "ingredient": "aspirin", "strength": "75 mg", "form": "tablet", "dose": "1 tablet",
     "frequency": "0-1-0", "instructions": "after lunch", "times": ["14:00"]},
    {"name": "Ibuprofen", "ingredient": "ibuprofen", "strength": "400 mg", "form": "tablet", "dose": "1 tablet",
     "frequency": "SOS", "instructions": "for knee pain, when needed", "times": []},
]

DISCHARGE_LINES = [
    ("h", "CITYCARE HEART INSTITUTE  (SYNTHETIC DEMO DOCUMENT)"),
    ("s", "Department of Cardiology  -  Discharge Summary"),
    ("", ""),
    ("", f"Patient Name: {PARENT}    Age/Sex: 74 / M"),
    ("", "UHID: CCH-2026-118204    IP No: 44817"),
    ("", "DOB: 14/02/1952    Mobile: +91 98220 41736"),
    ("", "Address: 12, Shanti Nagar, Aundh Road, Pune 411007"),
    ("", "Admitted: 19/10/2026    Discharged: 24/10/2026"),
    ("", "Consultant: Dr. Meera Deshpande"),
    ("", ""),
    ("b", "Diagnosis"),
    ("", "Non-ST elevation myocardial infarction (NSTEMI). Hypertension. Type 2 diabetes mellitus."),
    ("", ""),
    ("b", "Allergies"),
    ("", "Sulfa drugs - skin rash"),
    ("", ""),
    ("b", "Medicines on discharge"),
    ("", "1. Tab Clopidogrel 75 mg      1 tablet   1-0-0   after breakfast   - new, continue for 12 months"),
    ("", "2. Tab Metoprolol Succinate 25 mg   1 tablet   1-0-1   - new"),
    ("", "3. Tab Amlodipine 10 mg       1 tablet   1-0-0   - dose increased from 5 mg"),
    ("", "4. Tab Atorvastatin 40 mg     1 tablet   0-0-1   at bedtime   - dose increased from 20 mg"),
    ("", "5. Tab Aspirin 75 mg          1 tablet   0-1-0   after lunch   - continue"),
    ("", "6. Cap Omeprazole 20 mg       1 capsule  1-0-0   before breakfast   - new"),
    ("", "7. STOP Tab Ibuprofen. Avoid all NSAID painkillers; use Paracetamol 500 mg if needed."),
    ("", ""),
    ("b", "Advice"),
    ("", "Low salt, low fat diet. Walk 15 minutes twice daily. Report chest pain or breathlessness immediately."),
    ("", "Check blood pressure and pulse daily and keep a record."),
    ("", ""),
    ("b", "Follow-up"),
    ("", "Cardiology OPD review after 2 weeks (07/11/2026) with lipid profile and ECG."),
    ("", ""),
    ("", "Attendant: Anjali Kulkarni (daughter)   Contact No: 98901 22345"),
]


def write_discharge_pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    y = h - 22 * mm
    for style, text in DISCHARGE_LINES:
        if style == "h":
            c.setFont("Helvetica-Bold", 14)
        elif style == "s":
            c.setFont("Helvetica", 11)
        elif style == "b":
            c.setFont("Helvetica-Bold", 11)
        else:
            c.setFont("Helvetica", 10)
        if text:
            c.drawString(18 * mm, y, text)
        y -= 6.2 * mm if text else 3 * mm
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(18 * mm, 12 * mm, "Synthetic document for the KinShield demo. All names, numbers and details are invented.")
    c.save()
    return path


def write_scanned_png(pdf_path: Path, out: Path) -> Path:
    """Make a 'photographed' version: no text layer, slight rotation and noise, so the OCR path is exercised."""
    import pymupdf
    from PIL import Image, ImageFilter
    import random

    doc = pymupdf.open(pdf_path)
    pix = doc[0].get_pixmap(dpi=170)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    img = img.rotate(0.8, expand=True, fillcolor=(244, 241, 232))
    rnd = random.Random(7)
    px = img.load()
    for _ in range(img.width * img.height // 90):
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        v = rnd.randrange(150, 235)
        px[x, y] = (v, v, v - 8)
    img = img.filter(ImageFilter.GaussianBlur(0.5))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=85)
    return out


def seed(conn: sqlite3.Connection, ws_id: str) -> None:
    """Seed a workspace with the synthetic patient and the medicines he took before admission."""
    if conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]:
        return
    code = f"{ws_id}_{secrets.token_hex(4)}"
    with db.tx(conn):
        conn.execute("INSERT INTO people (role, name, telegram_link_code) VALUES ('caregiver', ?, ?)", (CAREGIVER, code))
        conn.execute("INSERT INTO people (role, name) VALUES ('parent', ?)", (PARENT,))
        actor = db.caregiver_id(conn)
        cids = []
        for m in EXISTING_MEDS:
            mid = careplan.add_medication(conn, m, source_item_id=None, confirmation_id=None, times=m["times"])
            cid = db.confirm(conn, actor, "medications", mid, "confirm", after={"seeded": m["name"]})
            conn.execute("UPDATE medications SET confirmation_id=?, ingredient_source='caregiver' WHERE id=?", (cid, mid))
            cids.append(cid)
        careplan.new_version(conn, "Medicines before admission (entered by caregiver)", cids)
        db.event(conn, "allergy", "Allergy: sulfa drugs (rash)", person_id=db.parent_id(conn))
        db.event(conn, "symptom", "Reported: dizzy when standing up quickly, twice this week",
                 person_id=db.parent_id(conn))
        db.event(conn, "note", "Synthetic demo workspace created. All data is invented.")


def ensure_demo_files(data_dir: Path) -> dict[str, Path]:
    d = data_dir / "demo-docs"
    pdf = d / "citycare-discharge-summary.pdf"
    png = d / "citycare-discharge-photo.jpg"
    if not pdf.exists():
        write_discharge_pdf(pdf)
    if not png.exists():
        write_scanned_png(pdf, png)
    return {"pdf": pdf, "photo": png}


def setup_owner(conn: sqlite3.Connection, ws_id: str, caregiver: str, parent: str, tz: str) -> None:
    code = f"{ws_id}_{secrets.token_hex(4)}"
    with db.tx(conn):
        conn.execute("INSERT INTO people (role, name, telegram_link_code, timezone) VALUES ('caregiver', ?, ?, ?)",
                     (caregiver, code, tz))
        conn.execute("INSERT INTO people (role, name, timezone) VALUES ('parent', ?, ?)", (parent, tz))
