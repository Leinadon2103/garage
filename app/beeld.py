"""Beeld van het voertuig: 3D-modellen (Sketchfab) en foto's (Wikimedia Commons) zoeken en opslaan."""
import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

lazy import httpx  # pas bij zoeken/opslaan laden; main.py gebruikt bij het opstarten alleen TOEGESTANE_HOSTS
lazy from PIL import Image, ImageOps

# Wikimedia weigert (403) een User-Agent zonder contactgegevens (https://w.wiki/4wJS); daarom de projectlink
HEADERS = {"User-Agent": "Garage/1.3 (https://github.com/Leinadon2103/garage)"}
TOEGESTANE_HOSTS = ("media.sketchfab.com", "upload.wikimedia.org", "thumb.wikimedia.org")
MAX_PX = 1600


class BeeldFout(Exception):
    pass


def _get(url: str, **params) -> httpx.Response:
    try:
        r = httpx.get(url, params=params, headers=HEADERS, timeout=10, follow_redirects=True)
        r.raise_for_status()
        return r
    except httpx.HTTPStatusError as e:
        raise BeeldFout(f"{e.request.url.host} weigert het verzoek (fout {e.response.status_code}). Probeer het later opnieuw.")
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
    """Eerst merk + volledig model; daarna breder: merk + eerste woord van het model, en merk + elke modelnaam
    (woord van alleen letters, bijv. 'GSF 600 BANDIT' -> 'Bandit': daar vindt Sketchfab vaak wel iets)."""
    merk, model = (merk or "").strip(), (model or "").strip()
    woorden = model.split()
    termen = [f"{merk} {model}".strip()]
    if len(woorden) > 1:
        termen.append(f"{merk} {woorden[0]}")
        termen += [f"{merk} {w}" for w in woorden[1:] if w.isalpha() and len(w) >= 3]
    return [t for t in dict.fromkeys(termen) if t]


def zoek_beide(zoek_modellen, zoek_fotos_) -> tuple[list[dict], list[dict], str | None]:
    """Beide bronnen tegelijk; faalt er één, dan blijven de resultaten van de andere zichtbaar."""
    with ThreadPoolExecutor(2) as pool:
        taken = pool.submit(zoek_modellen), pool.submit(zoek_fotos_)
    resultaten, fouten = [], []
    for taak in taken:
        try:
            resultaten.append(taak.result())
        except BeeldFout as e:
            resultaten.append([])
            fouten.append(str(e))
    return resultaten[0], resultaten[1], " ".join(dict.fromkeys(fouten)) or None


def _verzamel(zoeker, termen: list[str], sleutel: str) -> list[dict]:
    """Eerst de specifieke zoekterm; pas als dat weinig oplevert de bredere."""
    gevonden: list[dict] = []
    for term in termen:
        if len(gevonden) >= 6:
            break
        gevonden += [x for x in zoeker(term) if x[sleutel] not in {g[sleutel] for g in gevonden}]
    return gevonden[:9]


def zoek_alles(merk, model) -> tuple[list[dict], list[dict], str | None]:
    termen = zoektermen(merk, model)
    return zoek_beide(lambda: _verzamel(zoek_3d, termen, "uid"), lambda: _verzamel(zoek_fotos, termen, "thumb"))


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
