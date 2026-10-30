"""Generate the synthetic demo documents and a one-page guide. Everything here is invented.

    python scripts/make_demo_docs.py demo-data
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, A5
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from kinshield import demo  # noqa: E402

SYNTH = "Synthetic document for the KinShield demo. All names, numbers and details are invented."


def lines_pdf(path: Path, lines: list[tuple[str, str]], size=A4) -> Path:
    c = canvas.Canvas(str(path), pagesize=size)
    w, h = size
    y = h - 20 * mm
    for style, text in lines:
        font = {"h": ("Helvetica-Bold", 14), "s": ("Helvetica", 11), "b": ("Helvetica-Bold", 11)}.get(style, ("Helvetica", 10))
        c.setFont(*font)
        if text:
            c.drawString(16 * mm, y, text)
        y -= 6.2 * mm if text else 3 * mm
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(16 * mm, 10 * mm, SYNTH)
    c.save()
    return path


def photo_of(pdf: Path, out: Path, angle: float = 1.2, seed: int = 3) -> Path:
    doc = pymupdf.open(pdf)
    pix = doc[0].get_pixmap(dpi=180)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    img = img.rotate(angle, expand=True, fillcolor=(236, 232, 220))
    rnd = random.Random(seed)
    px = img.load()
    for _ in range(img.width * img.height // 80):
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        v = rnd.randrange(150, 235)
        px[x, y] = (v, v, v - 8)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img.save(out, quality=82)
    return out


FOLLOW_UP = [
    ("h", "CITYCARE HEART INSTITUTE  (SYNTHETIC DEMO DOCUMENT)"),
    ("s", "Cardiology OPD  -  Follow-up Prescription"),
    ("", ""),
    ("", f"Patient Name: {demo.PARENT}    Age/Sex: 74 / M"),
    ("", "UHID: CCH-2026-118204    OP No: 90317"),
    ("", "Visit date: 07/11/2026    Consultant: Dr. Meera Deshpande"),
    ("", "Mobile: +91 98220 41736"),
    ("", ""),
    ("b", "Findings"),
    ("", "BP 148/90, pulse 82. Lipid profile and ECG reviewed. Mild dizziness on standing reported."),
    ("", ""),
    ("b", "Medicines"),
    ("", "1. Tab Clopidogrel 75 mg       1 tablet   1-0-0   after breakfast   - continue"),
    ("", "2. Tab Metoprolol Succinate 50 mg   1 tablet   1-0-1   - dose increased from 25 mg"),
    ("", "3. Tab Amlodipine 10 mg        1 tablet   1-0-0   - continue"),
    ("", "4. Tab Atorvastatin 40 mg      1 tablet   0-0-1   at bedtime   - continue"),
    ("", "5. Tab Aspirin 75 mg           1 tablet   0-1-0   after lunch   - continue"),
    ("", "6. Tab Metformin 500 mg        1 tablet   1-0-1   after food   - continue"),
    ("", "7. STOP Cap Omeprazole."),
    ("", "8. Tab Pantoprazole 40 mg      1 tablet   1-0-0   before breakfast   - new, in place of omeprazole"),
    ("", ""),
    ("b", "Advice"),
    ("", "Rise slowly from bed or chair. Continue walking 15 minutes twice daily."),
    ("", ""),
    ("b", "Review"),
    ("", "Cardiology OPD review after 3 months with lipid profile."),
]

CLINIC = [
    ("h", "SUNRISE FAMILY CLINIC  (SYNTHETIC DEMO)"),
    ("s", "General physician prescription"),
    ("", ""),
    ("", f"Name: {demo.PARENT}    Age: 74"),
    ("", "Date: 12/11/2026    Dr. Arvind Kale"),
    ("", "Phone: 020 2553 1234"),
    ("", ""),
    ("b", "Complaint"),
    ("", "Dry cough and mild fever for 2 days."),
    ("", ""),
    ("b", "Rx"),
    ("", "1. Tab Paracetamol 500 mg   1 tablet   SOS for fever   - max 3 per day"),
    ("", "2. Syp Ambroxol 15 mg/5 ml   5 ml   1-1-1   after food   - for 5 days"),
    ("", ""),
    ("", "Steam inhalation twice daily. Plenty of warm fluids."),
    ("", "Review if fever lasts more than 3 days."),
]


def guide(path: Path) -> Path:
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9.5, leading=13)
    small = ParagraphStyle("s", parent=body, fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))
    h = ParagraphStyle("h", parent=ss["Heading2"], fontSize=12, spaceBefore=8, spaceAfter=4)
    P = lambda t, s=body: Paragraph(t, s)  # noqa: E731
    rows = [[P("<b>Order</b>"), P("<b>Upload this</b>"), P("<b>What it shows</b>")],
            [P("1"), P("<b>1-discharge-summary.pdf</b><br/>(or use Documents → Read the PDF)"),
             P("Reading and redaction; every field linked to its line on the page; confirming name, strength and dose. "
               "Then Decisions: 3 new medicines, 2 dose changes, Ibuprofen explicitly stopped, and Metformin "
               "<b>not mentioned</b> (stays until you decide). Medicines: the Clopidogrel label mentions Omeprazole.")],
            [P("2"), P("<b>2-follow-up-prescription.pdf</b>"),
             P("A later visit. Metoprolol dose increased (changed). Omeprazole explicitly stopped and Pantoprazole "
               "started (new). Metformin now explicitly continued. The new Pantoprazole gets its own label check "
               "against the rest of the list.")],
            [P("3"), P("<b>3-clinic-prescription-photo.jpg</b><br/>(or take a photo of it with Take a photo)"),
             P("The phone-photo path: no text layer, so the page is read with local OCR. Paracetamol is "
               "as-needed (no reminder times); Ambroxol syrup for 5 days. Medicines sold mainly outside the US may "
               "show \"No label information available\": the app says so instead of implying they are safe.")],
            [P("4"), P("<b>1-discharge-summary-photo.jpg</b>"),
             P("Optional: the same discharge as a tilted, noisy photo, to compare OCR with the PDF text.")]]
    t = Table(rows, colWidths=[14 * mm, 55 * mm, 105 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d0d6")),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f5"))]))
    story = [
        Paragraph("KinShield demo documents", ss["Title"]),
        P("Four invented documents for Ramesh, 74, the synthetic demo patient. Upload them in order in "
          "<b>Documents</b> after clicking <b>Start the demo</b>. Each one shows different features.", body),
        Spacer(1, 6), t,
        Paragraph("Then try", h),
        P("• <b>Medicines → Make a reminder due now</b>, then <b>More → Parent's screen</b>: tap <b>I need help</b>, "
          "or leave it for 2 minutes. A Telegram alert is sent; it shows as seen only after <b>Seen</b> is tapped."),
        P("• <b>Today → Note a symptom</b>, e.g. \"dizzy after morning tablets\". It goes into memory and the brief."),
        P("• <b>More → Ask KinShield</b>: \"What changed in the plan this week?\" Every sentence cites its record."),
        P("• <b>More → Visit brief → Prepare the brief</b>, then open the PDF to take to the doctor."),
        P("• <b>More → Activity log → What was sent</b>: the exact redacted text each Nemotron model received."),
        Paragraph("Notes", h),
        P("Names, phone numbers and ID numbers on these documents are there on purpose, so you can see them removed "
          "before anything is sent to a model.", small),
        P("KinShield organises what documents say. It does not give medical advice or judge whether medicines are "
          "safe. " + SYNTH, small),
    ]
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm,
                      bottomMargin=14 * mm, title="KinShield demo documents").build(story)
    return path


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    d1 = demo.write_discharge_pdf(out / "1-discharge-summary.pdf")
    demo.write_scanned_png(d1, out / "1-discharge-summary-photo.jpg")
    lines_pdf(out / "2-follow-up-prescription.pdf", FOLLOW_UP)
    clinic_pdf = lines_pdf(out / ".clinic-source.pdf", CLINIC, size=A5)
    photo_of(clinic_pdf, out / "3-clinic-prescription-photo.jpg", angle=-1.6, seed=11)
    clinic_pdf.unlink()
    guide(out / "0-README-demo-guide.pdf")
    for p in sorted(out.iterdir()):
        print(p.name, p.stat().st_size)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "demo-data"))
