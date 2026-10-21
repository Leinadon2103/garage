"""Tekstherkenning (OCR) met de ingebouwde Windows-OCR, via PowerShell (offline, geen extra pakketten).

Resultaat: woorden met hun kader als fracties (0-1) van de afbeelding, zodat ze direct bruikbaar zijn voor
de lak-suggesties, het weglakken van bedragen en het uitlezen van facturen.
"""
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

import pymupdf
from PIL import Image

SCRIPT = Path(__file__).with_name("ocr.ps1")
MAX_PX = 2600     # Windows-OCR weigert te grote afbeeldingen
MIN_PX = 1400     # kleine afbeeldingen eerst vergroten: dat herkent beter
TIMEOUT = 60
_cache: OrderedDict[str, list[dict]] = OrderedDict()


def beschikbaar() -> bool:
    return sys.platform == "win32" and SCRIPT.is_file()


def herken(im: Image.Image) -> list[dict]:
    """Woorden op een afbeelding: [{tekst, regel, x0, y0, x1, y1}] met fracties. Leeg als OCR niet lukt."""
    if not beschikbaar():
        return []
    im = im.convert("RGB")
    if max(im.size) > MAX_PX:
        im.thumbnail((MAX_PX, MAX_PX))
    elif max(im.size) < MIN_PX:
        factor = MIN_PX / max(im.size)
        im = im.resize((round(im.width * factor), round(im.height * factor)), Image.LANCZOS)
    sleutel = hashlib.sha1(im.tobytes()).hexdigest()
    if sleutel in _cache:
        _cache.move_to_end(sleutel)
        return _cache[sleutel]

    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        im.save(tmp, "PNG")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-File", str(SCRIPT), "-Pad", tmp], capture_output=True, timeout=TIMEOUT,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # geen flitsend venster
        if r.returncode != 0:
            logging.warning("OCR mislukt: %s", r.stderr.decode("utf-8", "replace")[:500])
            return []
        data = json.loads(r.stdout.decode("utf-8-sig"))
        b, h = data["breedte"], data["hoogte"]
        woorden = [{"tekst": w["t"], "regel": (0, w["r"]), "x0": w["x"] / b, "y0": w["y"] / h,
                    "x1": (w["x"] + w["b"]) / b, "y1": (w["y"] + w["h"]) / h} for w in data.get("woorden") or []]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, ZeroDivisionError):
        logging.exception("OCR mislukt")
        return []
    finally:
        Path(tmp).unlink(missing_ok=True)
    _cache[sleutel] = woorden
    while len(_cache) > 32:
        _cache.popitem(last=False)
    return woorden


def pagina(p: pymupdf.Page, dpi: int = 220) -> list[dict]:
    """OCR van een PDF-pagina (bijv. een scan zonder tekstlaag)."""
    pix = p.get_pixmap(dpi=dpi)
    return herken(Image.frombytes("RGB", (pix.width, pix.height), pix.samples) if pix.n == 3
                  else Image.frombytes("RGBA", (pix.width, pix.height), pix.samples).convert("RGB"))


def regels(woorden_per_pagina: list[list[dict]]) -> list[str]:
    """Tekstregels in leesvolgorde, per pagina."""
    uit = []
    for woorden in woorden_per_pagina:
        per_regel: dict = {}
        for w in woorden:
            per_regel.setdefault(w["regel"], []).append(w["tekst"])
        uit += [" ".join(ws) for _, ws in sorted(per_regel.items())]
    return uit
