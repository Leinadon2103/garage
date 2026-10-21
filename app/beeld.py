"""Beeld van het voertuig: 3D-modellen (Sketchfab) en foto's (Wikimedia Commons) zoeken en opslaan."""
import io
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps

HEADERS = {"User-Agent": "Voertuigonderhoud/1.0 (lokale app voor eigen gebruik)"}
TOEGESTANE_HOSTS = ("media.sketchfab.com", "upload.wikimedia.org", "thumb.wikimedia.org")
MAX_PX = 1600


class BeeldFout(Exception):
    pass


def _get(url: str, **params) -> httpx.Response:
    try:
        r = httpx.get(url, params=params, headers=HEADERS, timeout=10, follow_redirects=True)
        r.raise_for_status()
        return r
    except (httpx.HTTPError, httpx.InvalidURL):
        raise BeeldFout("Zoeken lukt niet: geen internet of de dienst is niet bereikbaar.")


def _json(r: httpx.Response) -> dict:
    try:
        return r.json()
    except ValueError:
        raise BeeldFout("De zoekdienst gaf een onverwacht antwoord.")


def zoek_3d(zoekterm: str, aantal: int = 9) -> list[dict]:
    data = _json(_get("https://api.sketchfab.com/v3/search", type="models", q=zoekterm, count=aantal))
    result = []
    for m in data.get("results", []):
        afbeeldingen = sorted(m.get("thumbnails", {}).get("images", []), key=lambda i: abs(i.get("width", 0) - 640))
        if afbeeldingen:
            result.append({"uid": m["uid"], "naam": m["name"], "thumb": afbeeldingen[0]["url"]})
    return result


def zoek_fotos(zoekterm: str, aantal: int = 9) -> list[dict]:
    data = _get("https://commons.wikimedia.org/w/api.php", action="query", generator="search",
                gsrsearch=f"{zoekterm} filetype:bitmap", gsrnamespace=6, gsrlimit=aantal, prop="imageinfo",
                iiprop="url", iiurlwidth=960, format="json")
    data = _json(data)
    paginas = sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    return [{"naam": p["title"].removeprefix("File:").rsplit(".", 1)[0], "thumb": p["imageinfo"][0]["thumburl"],
             "bron": p["imageinfo"][0].get("descriptionurl", "")} for p in paginas
            if p.get("imageinfo") and p["imageinfo"][0].get("thumburl")]


def zoektermen(merk: str | None, model: str | None) -> list[str]:
    """Eerst merk + volledig model; daarna breder (merk + eerste woord van het model)."""
    merk, model = (merk or "").strip(), (model or "").strip()
    termen = [f"{merk} {model}".strip()]
    if len(model.split()) > 1:
        termen.append(f"{merk} {model.split()[0]}")
    return [t for t in dict.fromkeys(termen) if t]


def zoek_alles(merk, model) -> tuple[list[dict], list[dict]]:
    modellen, fotos = [], []
    for term in zoektermen(merk, model):
        if len(modellen) < 6:
            modellen += [m for m in zoek_3d(term) if m["uid"] not in {x["uid"] for x in modellen}]
        if len(fotos) < 6:
            fotos += [f for f in zoek_fotos(term) if f["thumb"] not in {x["thumb"] for x in fotos}]
    return modellen[:9], fotos[:9]


def _opslaan(inhoud: bytes, map_: Path) -> str:
    """Slaat op als JPEG zonder EXIF (geen GPS-locatie) en geeft de bestandsnaam terug."""
    try:
        with Image.open(io.BytesIO(inhoud)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.info.clear()  # geen EXIF/commentaar (kan locatie of naam bevatten)
            im.thumbnail((MAX_PX, MAX_PX))
            map_.mkdir(parents=True, exist_ok=True)
            naam = f"{uuid.uuid4().hex}.jpg"
            im.save(map_ / naam, "JPEG", quality=88)
            return naam
    except (OSError, ValueError, Image.DecompressionBombError):
        raise BeeldFout("Dit bestand is geen geldige afbeelding.")


def download(url: str, map_: Path) -> str:
    if urlparse(url).hostname not in TOEGESTANE_HOSTS:
        raise BeeldFout("Onbekende afbeeldingsbron.")
    r = _get(url)
    if r.url.host not in TOEGESTANE_HOSTS:  # ook na een doorverwijzing
        raise BeeldFout("Onbekende afbeeldingsbron.")
    return _opslaan(r.content, map_)


def eigen_foto(inhoud: bytes, map_: Path) -> str:
    return _opslaan(inhoud, map_)
