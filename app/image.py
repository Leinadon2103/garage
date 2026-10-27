"""Image of the vehicle: search and save 3D models (Sketchfab) and photos (Wikimedia Commons)."""
import io
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

lazy import httpx  # load only when searching/saving; at startup main.py only uses ALLOWED_HOSTS
lazy from PIL import Image, ImageOps

# Wikimedia refuses (403) a User-Agent without contact details (https://w.wiki/4wJS); hence the project link
HEADERS = {"User-Agent": "Garage/1.3 (https://github.com/Leinadon2103/garage)"}
ALLOWED_HOSTS = ("media.sketchfab.com", "upload.wikimedia.org", "thumb.wikimedia.org")
MAX_PX = 1600


class ImageError(Exception):
    pass


def _get(url: str, **params) -> httpx.Response:
    try:
        r = httpx.get(url, params=params, headers=HEADERS, timeout=10, follow_redirects=True)
        r.raise_for_status()
        return r
    except httpx.HTTPStatusError as e:
        raise ImageError(f"{e.request.url.host} weigert het verzoek (fout {e.response.status_code}). Probeer het later opnieuw.")
    except (httpx.HTTPError, httpx.InvalidURL):
        raise ImageError("Zoeken lukt niet: geen internet of de dienst is niet bereikbaar.")


def _json(r: httpx.Response) -> dict:
    try:
        return r.json()
    except ValueError:
        raise ImageError("De zoekdienst gaf een onverwacht antwoord.")


def search_3d(query: str, count: int = 9) -> list[dict]:
    data = _json(_get("https://api.sketchfab.com/v3/search", type="models", q=query, count=count))
    result = []
    for m in data.get("results", []):
        images = sorted(m.get("thumbnails", {}).get("images", []), key=lambda i: abs(i.get("width", 0) - 640))
        if images:
            result.append({"uid": m["uid"], "name": m["name"], "thumb": images[0]["url"]})
    return result


def search_photos(query: str, count: int = 9) -> list[dict]:
    data = _get("https://commons.wikimedia.org/w/api.php", action="query", generator="search",
                gsrsearch=f"{query} filetype:bitmap", gsrnamespace=6, gsrlimit=count, prop="imageinfo",
                iiprop="url", iiurlwidth=960, format="json")
    data = _json(data)
    pages = sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    return [{"name": p["title"].removeprefix("File:").rsplit(".", 1)[0], "thumb": p["imageinfo"][0]["thumburl"],
             "source": p["imageinfo"][0].get("descriptionurl", "")} for p in pages
            if p.get("imageinfo") and p["imageinfo"][0].get("thumburl")]


def search_terms(make: str | None, model: str | None) -> list[str]:
    """First make + full model; then broader: make + first word of the model, and make + each model name
    (a word of only letters, e.g. 'GSF 600 BANDIT' -> 'Bandit': Sketchfab often finds something for that)."""
    make, model = (make or "").strip(), (model or "").strip()
    words = model.split()
    terms = [f"{make} {model}".strip()]
    if len(words) > 1:
        terms.append(f"{make} {words[0]}")
        terms += [f"{make} {w}" for w in words[1:] if w.isalpha() and len(w) >= 3]
    return [t for t in dict.fromkeys(terms) if t]


def search_both(search_models, search_photos_) -> tuple[list[dict], list[dict], str | None]:
    """Both sources at once; if one fails, the results of the other stay visible."""
    with ThreadPoolExecutor(2) as pool:
        tasks = pool.submit(search_models), pool.submit(search_photos_)
    results, errors = [], []
    for task in tasks:
        try:
            results.append(task.result())
        except ImageError as e:
            results.append([])
            errors.append(str(e))
    return results[0], results[1], " ".join(dict.fromkeys(errors)) or None


def _collect(searcher, terms: list[str], key: str) -> list[dict]:
    """The specific search term first; the broader ones only if that finds little."""
    found: list[dict] = []
    for term in terms:
        if len(found) >= 6:
            break
        found += [x for x in searcher(term) if x[key] not in {f[key] for f in found}]
    return found[:9]


def search_all(make, model) -> tuple[list[dict], list[dict], str | None]:
    terms = search_terms(make, model)
    return search_both(lambda: _collect(search_3d, terms, "uid"), lambda: _collect(search_photos, terms, "thumb"))


def _clean(content: bytes) -> bytes:
    """As JPEG without EXIF (no GPS location), at most MAX_PX; the caller saves it with db.write_file."""
    try:
        with Image.open(io.BytesIO(content)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.info.clear()  # no EXIF/comment (could contain a location or name)
            im.thumbnail((MAX_PX, MAX_PX))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88)
            return buf.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ImageError("Dit bestand is geen geldige afbeelding.")


def download(url: str) -> bytes:
    if urlparse(url).hostname not in ALLOWED_HOSTS:
        raise ImageError("Onbekende afbeeldingsbron.")
    r = _get(url)
    if r.url.host not in ALLOWED_HOSTS:  # also after a redirect
        raise ImageError("Onbekende afbeeldingsbron.")
    return _clean(r.content)


def own_photo(content: bytes) -> bytes:
    return _clean(content)
