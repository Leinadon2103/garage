"""Garage – start de app als eigen Windows-programma.

De webserver draait onzichtbaar in dit proces en de schermen verschijnen in een eigen programmavenster
(pywebview met de WebView2-engine van Windows). Venster dicht = programma klaar.

Ontwikkelen: `.venv\\Scripts\\pythonw.exe Garage.pyw` (gegevens in .\\data).
Geïnstalleerd (Garage.exe via PyInstaller): gegevens in Documenten\\Garage.
"""
import ctypes
import logging
import os
import socket
import sys
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path

BEVROREN = getattr(sys, "frozen", False)  # True in Garage.exe
HIER = Path(sys.executable).parent if BEVROREN else Path(__file__).resolve().parent
# ook gebruikt door het installatieprogramma (AppMutex); GARAGE_MUTEX alleen voor tests naast een draaiende Garage
MUTEX_NAAM = os.environ.get("GARAGE_MUTEX", "Garage-Voertuigonderhoud")
TITEL = "Garage"
APP_ID = "Garage.Voertuigonderhoud"  # Windows-app-id voor meldingen (ook in installer.iss)
# (achtergrond, tekst, rand) gelijk aan --balk / --text / --line in app/static/app.css (licht en donker),
# zodat titelbalk en app-bovenbalk één geheel vormen
BALK_LICHT = ("#ffffff", "#14171c", "#e4e7ec")
BALK_DONKER = ("#181b21", "#eceef2", "#2a2f38")
BALK_ACHTERGROND = BALK_LICHT[0]


def documenten() -> Path:
    """De echte Documenten-map, ook als die door OneDrive is verplaatst."""
    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", wintypes.BYTE * 8)]

    guid = GUID.from_buffer_copy(uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7").bytes_le)  # FOLDERID_Documents
    pad = ctypes.c_wchar_p()
    try:
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(pad)) == 0:
            return Path(pad.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(pad)
    return Path.home() / "Documents"


def data_map() -> Path:
    if os.environ.get("ONDERHOUD_DATA"):
        return Path(os.environ["ONDERHOUD_DATA"])
    return documenten() / "Garage" if BEVROREN else HIER / "data"


def log_naar(data: Path) -> None:
    """app.log in de gegevensmap; hooguit 1 MB plus 2 oude delen, zodat het log niet blijft groeien."""
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(data / "app.log", maxBytes=1024 ** 2, backupCount=2, encoding="utf-8")
    logging.basicConfig(handlers=[handler], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def vrije_poort() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def melding(tekst: str) -> None:
    """Zichtbare foutmelding (er is geen console)."""
    ctypes.windll.user32.MessageBoxW(None, tekst, TITEL, 0x10)


def al_open() -> bool:
    """Eén Garage tegelijk: is er al een, zet die dan naar voren."""
    global _mutex
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    _mutex = kernel32.CreateMutexW(None, False, MUTEX_NAAM)  # blijft bestaan tot het proces stopt
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return False
    venster = zoek_garage_venster()
    if venster:
        ctypes.windll.user32.ShowWindow(venster, 9)  # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(venster)
    return True


def zoek_garage_venster() -> int | None:
    """Het venster 'Garage' van een ander Garage-proces (niet bijv. een Verkenner-venster van de map Garage)."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    eigen_exe = Path(sys.executable).name.lower()
    gevonden = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def per_venster(hwnd, _):
        titel = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(hwnd, titel, 64)
        if titel.value == TITEL and user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            proces = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
            if proces:
                pad, lengte = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
                if kernel32.QueryFullProcessImageNameW(proces, 0, pad, ctypes.byref(lengte)) \
                        and Path(pad.value).name.lower() == eigen_exe:
                    gevonden.append(hwnd)
                kernel32.CloseHandle(proces)
        return True

    user32.EnumWindows(per_venster, 0)
    return gevonden[0] if gevonden else None


def _rgb(hexkleur: str) -> tuple[int, int, int]:
    return tuple(int(hexkleur[i:i + 2], 16) for i in (1, 3, 5))


def mix(van: tuple, naar: tuple, t: float) -> tuple:
    """Kleuren (hex) mengen: t=0 -> van, t=1 -> naar."""
    return tuple("#%02x%02x%02x" % tuple(round(a + (b - a) * t) for a, b in zip(_rgb(k1), _rgb(k2)))
                 for k1, k2 in zip(van, naar))


def kleur_titelbalk(hwnd: int, kleuren: tuple = BALK_LICHT, donker: bool | None = None) -> None:
    """Titelbalk in de kleur van de app-bovenbalk (Windows 11; op oudere Windows gebeurt er niets)."""
    dwm = ctypes.windll.dwmapi
    if donker is not None:  # lichte of donkere knoppen (minimaliseren, sluiten, ...)
        waarde = ctypes.c_int(1 if donker else 0)
        dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), 20, ctypes.byref(waarde), 4)  # USE_IMMERSIVE_DARK_MODE
    for attribuut, kleur in zip((35, 36, 34), kleuren):  # caption, tekst, rand
        r, g, b = _rgb(kleur)
        waarde = ctypes.c_int(b << 16 | g << 8 | r)  # COLORREF 0x00bbggrr
        dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), attribuut, ctypes.byref(waarde), 4)


def windows_donker() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def start_donker(thema: str) -> bool:
    """Eerste gok voor de vensterkleur (de pagina corrigeert dit direct, ook voor de stand 'zon')."""
    return thema == "donker" or (thema == "systeem" and windows_donker())


class Api:
    """Functies die de pagina's via window.pywebview.api kunnen aanroepen."""

    def __init__(self, basis_url: str, donker: bool = False):
        self.basis_url = basis_url
        self.hwnd = None
        self._kleuren = BALK_DONKER if donker else BALK_LICHT
        self._slot = threading.Lock()

    def titelbalk(self, donker: bool, vloeiend: bool = False) -> None:
        """Titelbalk laten meekleuren met de weergave; bij een wissel vloeiend in 0,6 s."""
        if self.hwnd:
            threading.Thread(target=self._kleur, args=(bool(donker), bool(vloeiend)), daemon=True).start()

    def _kleur(self, donker: bool, vloeiend: bool) -> None:
        doel = BALK_DONKER if donker else BALK_LICHT
        with self._slot:
            stappen = 20 if vloeiend and doel != self._kleuren else 1
            for i in range(1, stappen + 1):
                kleur_titelbalk(self.hwnd, mix(self._kleuren, doel, i / stappen), donker if i == stappen else None)
                if stappen > 1:
                    time.sleep(0.6 / stappen)
            self._kleuren = doel

    def kies_map(self) -> str | None:
        """Map kiezen voor de back-ups (Windows-mapkiezer)."""
        import webview
        keuze = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER)
        return (keuze if isinstance(keuze, str) else keuze[0]) if keuze else None

    def opslaan(self, pad: str, naam: str) -> str | None:
        """Verkoop-PDF opslaan via een Windows 'Opslaan als'-venster. Geeft het gekozen pad terug (of None)."""
        import re
        import urllib.request
        import webview
        if not re.fullmatch(r"/voertuigen/\d+/export\.pdf(\?[\w=&]*)?", pad):
            raise ValueError("Ongeldige export")
        keuze = webview.windows[0].create_file_dialog(
            webview.FileDialog.SAVE, directory=str(Path.home() / "Downloads"), save_filename=naam,
            file_types=("PDF bestand (*.pdf)",))  # pywebview: alleen letters/spaties in de omschrijving
        if not keuze:
            return None  # geannuleerd
        doel = Path(keuze if isinstance(keuze, str) else keuze[0])
        # bandit B310 mag hier: altijd http://127.0.0.1 + een pad dat hierboven is gecontroleerd
        with urllib.request.urlopen(self.basis_url + pad, timeout=120) as r:  # nosec B310
            doel.write_bytes(r.read())
        return str(doel)


def start_server(poort: int):
    import uvicorn
    from app.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=poort, log_config=None, access_log=False))
    draad = threading.Thread(target=server.run, daemon=True)
    draad.start()
    for _ in range(300):
        if server.started:
            return server, draad
        if not draad.is_alive():
            break
        time.sleep(0.05)
    raise RuntimeError("De interne server van Garage kon niet starten.")


def toon_melding(titel: str, tekst: str) -> bool:
    """Windows-melding (toast) via PowerShell; klikken opent Garage."""
    import base64
    import json
    import subprocess
    script = (HIER / "_internal" if BEVROREN else HIER) / "app" / "melding.ps1"
    gegevens = base64.b64encode(json.dumps({"titel": titel, "tekst": tekst}).encode()).decode()
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                        "-Gegevens", gegevens, "-AppId", APP_ID], timeout=60, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        logging.warning("Melding tonen mislukt: %s", r.stderr.decode("utf-8", "replace")[:300])
    return r.returncode == 0


def controle() -> None:
    """'Garage.exe --controle' (bij het aanmelden in Windows): melding als er onderhoud nodig is. Geen venster."""
    data = data_map()
    if not (data / "onderhoud.db").exists():
        return
    os.environ["ONDERHOUD_DATA"] = str(data)
    log_naar(data)
    sys.path.insert(0, str(HIER))
    try:
        from app import db, extra
        db.init()
        regels = extra.controle_meldingen()
        if extra.mag_melden(regels):
            teksten = [t for _, t in regels]
            tekst = "\n".join(teksten[:3]) + (f"\n…en nog {len(teksten) - 3}" if len(teksten) > 3 else "")
            if toon_melding("Garage: er is iets te doen", tekst):
                extra.gemeld(regels)
                logging.info("Herinnering getoond (%d punten)", len(regels))
    except Exception:  # noqa: BLE001 - op de achtergrond nooit een foutvenster tonen
        logging.exception("Controle mislukt")


def zelftest(verslag: Path | None = None) -> bool:
    """'Garage.exe --zelftest [verslag.txt]' (bouwen.bat): start de server met lege gegevens in een tijdelijke map,
    doorloopt de belangrijkste schermen (ook die met PDF's, afbeeldingen en OCR) en stopt. Vangt fouten die alleen
    in de gebouwde exe optreden, zoals een ontbrekende module of een weggelaten Pillow-onderdeel.
    Geen venster, en de echte gegevens worden niet aangeraakt."""
    import tempfile
    import traceback
    sys.path.insert(0, str(HIER))
    regels = []
    with tempfile.TemporaryDirectory(prefix="garage-zelftest-", ignore_cleanup_errors=True) as tmp:
        try:
            from app import db
            oude_map, db.DATA_DIR = db.DATA_DIR, Path(tmp).resolve()  # zoals db.DATA_DIR zelf (geen 8.3-pad)
            try:
                db.init()
                with db.connect() as conn:
                    db.zet_instelling(conn, "backup_auto", "uit")  # nooit een back-up naar de echte back-upmap
                    olie = next(t["id"] for t in db.types_voor(conn, "Motorfiets") if t["naam"] == "Olie vervangen")
                server, draad = start_server(vrije_poort())
                try:
                    _zelftest_stappen(f"http://127.0.0.1:{server.config.port}", olie, regels)
                finally:
                    server.should_exit = True
                    draad.join(timeout=10)
            finally:
                db.DATA_DIR = oude_map
            gelukt = True
        except Exception:  # noqa: BLE001 - elke fout hoort in het verslag
            regels.append("FOUT " + traceback.format_exc())
            gelukt = False
    if verslag:
        verslag.write_text("\n".join(regels + ["Zelftest " + ("geslaagd" if gelukt else "MISLUKT")]), encoding="utf-8")
    return gelukt


def _zelftest_stappen(basis: str, olie: int, regels: list[str]) -> None:
    import io
    import json
    import httpx
    import pymupdf
    from PIL import Image

    def stap(omschrijving: str, r: httpx.Response, status: int = 200) -> httpx.Response:
        if r.status_code != status:
            raise RuntimeError(f"{omschrijving}: status {r.status_code}, verwacht {status}\n{r.text[:1000]}")
        regels.append(f"OK   {omschrijving}")
        return r

    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Factuur zelftest")
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, "JPEG")
    jpg = buf.getvalue()
    facturen = [("facturen", ("factuur.pdf", pdf.tobytes(), "application/pdf")),
                ("facturen", ("factuur.jpg", jpg, "image/jpeg"))]

    with httpx.Client(base_url=basis, timeout=120) as c:
        for pad in ("/gezond", "/", "/instellingen", "/voertuigen/nieuw", "/static/app.css"):
            stap(f"GET {pad}", c.get(pad))
        v = stap("voertuig toevoegen", c.post("/voertuigen", data={
            "kenteken": "ZT-01-ST", "voertuigsoort": "Motorfiets", "merk": "Test"}), 303).headers["location"]
        for sub in ("", "/kosten", "/documenten", "/specificaties", "/stalling", "/intervallen"):
            stap(f"GET {v}{sub}", c.get(v + sub))
        stap("factuur uitlezen (OCR)", c.post(f"{v}/factuur-uitlezen", files=facturen))
        lakken = stap("beurt met facturen opslaan", c.post(f"{v}/beurt/nieuw", files=facturen, data={
            "datum": "2026-01-15", "kilometerstand": "12000", "types": [str(olie)]}), 303).headers["location"]
        for _ in facturen:
            factuur = stap("naar de volgende factuur", c.get(lakken), 303).headers["location"]
            stap(f"GET {factuur}", c.get(factuur))
            stap("pagina als afbeelding", c.get(factuur.replace("/lakken", "/pagina/0.png")))
            balk = [{"pagina": 0, "x0": 0.1, "y0": 0.1, "x1": 0.4, "y1": 0.2}]
            stap("weglakken", c.post(factuur, data={"boxen": json.dumps(balk)}), 303)
        if not stap("verkoop-PDF", c.get(f"{v}/export.pdf")).content.startswith(b"%PDF"):
            raise RuntimeError("verkoop-PDF is geen PDF")
        stap("eigen foto opslaan", c.post(f"{v}/beeld/upload", files={"foto": ("foto.jpg", jpg, "image/jpeg")}), 303)
        stap("foto tonen", c.get(f"{v}/beeld.jpg"))


def main() -> None:
    if "--controle" in sys.argv:
        controle()
        return
    if "--zelftest" in sys.argv:
        rest = sys.argv[sys.argv.index("--zelftest") + 1:]
        sys.exit(0 if zelftest(Path(rest[0]) if rest else None) else 1)
    if al_open():
        return
    data = data_map()
    data.mkdir(parents=True, exist_ok=True)
    os.environ["ONDERHOUD_DATA"] = str(data)  # vóór het importeren van de app (db.DATA_DIR)
    log_naar(data)
    sys.path.insert(0, str(HIER))
    try:
        import webview
        from app import backup, db
        backup.opruimen()
        db.init()  # hier al, zodat een fout (bijv. gegevens van een nieuwere versie) in de melding komt
        server, draad = start_server(vrije_poort())
        with db.connect() as conn:
            donker = start_donker(db.instelling(conn, "thema", "systeem"))
        basis = f"http://127.0.0.1:{server.config.port}"
        api = Api(basis, donker)
        venster = webview.create_window(TITEL, basis, js_api=api, width=1280, height=860, min_size=(420, 600),
                                        background_color="#0e1014" if donker else "#f5f6f8", text_select=True)

        def bij_tonen():
            try:
                api.hwnd = int(venster.native.Handle.ToInt64())
                kleur_titelbalk(api.hwnd, BALK_DONKER if donker else BALK_LICHT, donker)
            except Exception:  # noqa: BLE001 - puur cosmetisch, nooit de app laten falen
                logging.exception("Titelbalk kleuren mislukt")

        venster.events.shown += bij_tonen
        webview.start(gui="edgechromium")  # blokkeert tot het venster dicht is
        server.should_exit = True
        draad.join(timeout=5)
    except Exception as e:
        logging.exception("Garage kon niet starten")
        tip = "\n\nControleer of 'Microsoft Edge WebView2 Runtime' is geïnstalleerd." if "webview" in str(e).lower() else ""
        melding(f"Garage kon niet starten:\n{e}{tip}\n\nDetails: {data / 'app.log'}")


if __name__ == "__main__":
    main()
