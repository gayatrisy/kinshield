"""A realistic-looking phone photo of a printed clinic prescription pad (synthetic)."""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation"]


def font(names: list[str], size: int) -> ImageFont.FreeTypeFont:
    for d in FONT_DIRS:
        for n in names:
            p = Path(d) / n
            if p.exists():
                return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def make(out: Path) -> Path:
    W, H = 1240, 1754  # A5 at ~210 dpi
    pad = Image.new("RGB", (W, H), (252, 251, 246))
    d = ImageDraw.Draw(pad)
    bold = lambda s: font(["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"], s)  # noqa: E731
    reg = lambda s: font(["DejaVuSans.ttf", "LiberationSans-Regular.ttf"], s)  # noqa: E731
    serif = lambda s: font(["DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf"], s)  # noqa: E731
    blue, ink = (24, 72, 140), (25, 25, 30)
    # letterhead
    d.rectangle([0, 0, W, 210], fill=(232, 240, 250))
    d.rectangle([0, 205, W, 214], fill=blue)
    d.text((70, 40), "SUNRISE FAMILY CLINIC", font=bold(54), fill=blue)
    d.text((70, 112), "Dr. Arvind Kale, MBBS, MD (Medicine)   Reg. No. MMC-58213", font=reg(26), fill=blue)
    d.text((70, 150), "Baner Road, Pune   Phone: 020 2553 1234   (SYNTHETIC DEMO)", font=reg(24), fill=blue)
    y = 260
    d.text((70, y), "Name: Ramesh Kulkarni", font=reg(32), fill=ink)
    d.text((760, y), "Age/Sex: 74 / M", font=reg(32), fill=ink)
    y += 52
    d.text((70, y), "Date: 12/11/2026", font=reg(32), fill=ink)
    d.text((760, y), "Wt: 68 kg", font=reg(32), fill=ink)
    y += 70
    d.text((70, y), "Complaint: Dry cough and mild fever for 2 days.", font=reg(30), fill=ink)
    y += 46
    d.text((70, y), "BP 136/84   Temp 99.6 F   SpO2 97%", font=reg(30), fill=ink)
    y += 90
    d.text((70, y - 10), "Rx", font=serif(84), fill=blue)
    y += 110
    rx = [
        "1. Tab Paracetamol 500 mg   1 tablet   SOS for fever",
        "      (not more than 3 tablets in a day)",
        "2. Syp Ambroxol 15 mg/5 ml   5 ml   1-1-1   after food",
        "      for 5 days",
        "3. Tab Levocetirizine 5 mg   1 tablet   0-0-1   at night",
        "      for 5 days",
    ]
    for line in rx:
        d.text((90, y), line, font=reg(34), fill=ink)
        y += 58
    y += 40
    d.text((70, y), "Advice: Steam inhalation twice daily. Warm fluids.", font=reg(30), fill=ink)
    y += 46
    d.text((70, y), "Review if fever lasts more than 3 days.", font=reg(30), fill=ink)
    # signature scribble and stamp
    rnd = random.Random(5)
    sx, sy = 820, H - 330
    pts = [(sx + i * 9, sy + 22 * math.sin(i / 3.0) + rnd.randint(-6, 6)) for i in range(38)]
    d.line(pts, fill=(20, 40, 110), width=4)
    d.text((820, H - 270), "Dr. Arvind Kale", font=reg(26), fill=ink)
    d.ellipse([110, H - 380, 360, H - 130], outline=(150, 40, 60), width=5)
    d.text((150, H - 275), "SUNRISE", font=bold(30), fill=(150, 40, 60))
    d.text((165, H - 235), "CLINIC", font=bold(30), fill=(150, 40, 60))
    d.text((70, H - 70), "Synthetic document for the KinShield demo. All names, numbers and details are invented.",
           font=reg(20), fill=(110, 110, 110))

    # the "photo": table background, slight rotation, uneven light, sensor noise
    bg = Image.new("RGB", (W + 260, H + 260), (120, 92, 70))
    g = ImageDraw.Draw(bg)
    for i in range(0, bg.height, 14):  # wood-ish grain
        shade = 110 + int(14 * math.sin(i / 37.0))
        g.line([(0, i), (bg.width, i + 30)], fill=(shade + 15, shade - 10, shade - 35), width=7)
    shadow = Image.new("L", (W, H), 0)
    bg.paste((60, 45, 35), (142, 146), shadow.point(lambda _: 120))
    bg.paste(pad, (130, 130))
    photo = bg.rotate(-2.4, resample=Image.BICUBIC, expand=False, fillcolor=(110, 85, 65))
    # uneven light: darker towards the bottom right
    light = Image.new("L", photo.size, 0)
    ld = ImageDraw.Draw(light)
    for r in range(0, max(photo.size), 8):
        ld.ellipse([photo.width * 0.25 - r, photo.height * 0.2 - r, photo.width * 0.25 + r, photo.height * 0.2 + r],
                   outline=min(90, r // 22))
    dark = Image.new("RGB", photo.size, (0, 0, 0))
    photo = Image.composite(dark, photo, light.filter(ImageFilter.GaussianBlur(40)))
    px = photo.load()
    for _ in range(photo.width * photo.height // 70):
        x, y2 = rnd.randrange(photo.width), rnd.randrange(photo.height)
        r0, g0, b0 = px[x, y2]
        n = rnd.randint(-22, 22)
        px[x, y2] = (max(0, min(255, r0 + n)), max(0, min(255, g0 + n)), max(0, min(255, b0 + n)))
    photo = photo.filter(ImageFilter.GaussianBlur(0.7)).crop((60, 60, photo.width - 60, photo.height - 60))
    photo.save(out, quality=84)
    return out


if __name__ == "__main__":
    print(make(Path(sys.argv[1] if len(sys.argv) > 1 else "demo-data/3-clinic-prescription-photo.jpg")))
