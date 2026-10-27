"""Text recognition (OCR) with the built-in Windows OCR, via PowerShell (offline, no extra packages).

Result: words with their box as fractions (0-1) of the image, ready for the redaction suggestions, hiding
amounts and reading invoices.
"""
import base64
import hashlib
import io
import json
import logging
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

import pymupdf
from PIL import Image

SCRIPT = Path(__file__).with_name("ocr.ps1")
MAX_PX = 2600     # Windows OCR refuses images that are too large
MIN_PX = 1400     # enlarge small images first: recognition is better
TIMEOUT = 60
_cache: OrderedDict[str, list[dict]] = OrderedDict()


def available() -> bool:
    return sys.platform == "win32" and SCRIPT.is_file()


def recognize(im: Image.Image) -> list[dict]:
    """Words on an image: [{text, line, x0, y0, x1, y1}] as fractions. Empty if OCR fails."""
    if not available():
        return []
    im = im.convert("RGB")
    if max(im.size) > MAX_PX:
        im.thumbnail((MAX_PX, MAX_PX))
    elif max(im.size) < MIN_PX:
        factor = MIN_PX / max(im.size)
        im = im.resize((round(im.width * factor), round(im.height * factor)), Image.LANCZOS)
    key = hashlib.sha1(im.tobytes(), usedforsecurity=False).hexdigest()  # only a cache key
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]

    try:
        png = io.BytesIO()
        im.save(png, "PNG")
        # via stdin, not a temporary file: that would be a readable copy of the invoice after a crash (P8)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-File", str(SCRIPT)], input=base64.b64encode(png.getvalue()), capture_output=True,
                           timeout=TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # no flashing window
        if r.returncode != 0:
            logging.warning("OCR failed: %s", r.stderr.decode("utf-8", "replace")[:500])
            return []
        data = json.loads(r.stdout.decode("utf-8-sig"))
        w_, h_ = data["width"], data["height"]
        words = [{"text": w["t"], "line": (0, w["l"]), "x0": w["x"] / w_, "y0": w["y"] / h_,
                  "x1": (w["x"] + w["w"]) / w_, "y1": (w["y"] + w["h"]) / h_} for w in data.get("words") or []]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, ZeroDivisionError):
        logging.exception("OCR failed")
        return []
    _cache[key] = words
    while len(_cache) > 32:
        _cache.popitem(last=False)
    return words


def page(p: pymupdf.Page, dpi: int = 220) -> list[dict]:
    """OCR of a PDF page (e.g. a scan without a text layer)."""
    pix = p.get_pixmap(dpi=dpi)
    return recognize(Image.frombytes("RGB", (pix.width, pix.height), pix.samples) if pix.n == 3
                     else Image.frombytes("RGBA", (pix.width, pix.height), pix.samples).convert("RGB"))


def lines(words_per_page: list[list[dict]]) -> list[str]:
    """Text lines in reading order, per page."""
    out = []
    for words in words_per_page:
        per_line: dict = {}
        for w in words:
            per_line.setdefault(w["line"], []).append(w["text"])
        out += [" ".join(ws) for _, ws in sorted(per_line.items())]
    return out
