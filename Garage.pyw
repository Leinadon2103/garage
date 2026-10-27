"""Garage - starts the app as a Windows program of its own.

The web server runs invisibly in this process and the screens appear in a program window of their own
(pywebview with the Windows WebView2 engine). Window closed = program done.

Development: `.venv\\Scripts\\pythonw.exe Garage.pyw` (data in .\\data).
Installed (Garage.exe via PyInstaller): data in Documents\\Garage.
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

FROZEN = getattr(sys, "frozen", False)  # True in Garage.exe
HERE = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent
# also used by the installer (AppMutex); GARAGE_MUTEX only for tests next to a running Garage
MUTEX_NAME = os.environ.get("GARAGE_MUTEX", "Garage-Voertuigonderhoud")
TITLE = "Garage"
APP_ID = "Garage.Voertuigonderhoud"  # Windows app id for notifications (also in installer.iss)
DATA_ENV = "GARAGE_DATA"             # overrides the data folder; ONDERHOUD_DATA is the old name and still works
# (background, text, border) equal to --bar / --text / --line in app/static/app.css (light and dark),
# so the title bar and the app's top bar look like one piece
BAR_LIGHT = ("#ffffff", "#14171c", "#e4e7ec")
BAR_DARK = ("#181b21", "#eceef2", "#2a2f38")


def documents() -> Path:
    """The real Documents folder, also when OneDrive has moved it."""
    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", wintypes.BYTE * 8)]

    guid = GUID.from_buffer_copy(uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7").bytes_le)  # FOLDERID_Documents
    path = ctypes.c_wchar_p()
    try:
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0:
            return Path(path.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path)
    return Path.home() / "Documents"


def data_folder() -> Path:
    if os.environ.get(DATA_ENV) or os.environ.get("ONDERHOUD_DATA"):
        return Path(os.environ.get(DATA_ENV) or os.environ["ONDERHOUD_DATA"])
    return documents() / "Garage" if FROZEN else HERE / "data"


def log_to(data: Path) -> None:
    """app.log in the data folder; at most 1 MB plus 2 old parts, so the log does not keep growing."""
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(data / "app.log", maxBytes=1024 ** 2, backupCount=2, encoding="utf-8")
    logging.basicConfig(handlers=[handler], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def message_box(text: str) -> None:
    """Visible error message (there is no console)."""
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10)


def already_running() -> bool:
    """One Garage at a time: if there is one already, bring it to the front."""
    global _mutex
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    _mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)  # exists until the process stops
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return False
    window = find_garage_window()
    if window:
        ctypes.windll.user32.ShowWindow(window, 9)  # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(window)
    return True


def find_garage_window() -> int | None:
    """The 'Garage' window of another Garage process (not e.g. an Explorer window of the folder Garage)."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    own_exe = Path(sys.executable).name.lower()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def per_window(hwnd, _):
        title = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(hwnd, title, 64)
        if title.value == TITLE and user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
            if process:
                path, length = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
                if kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(length)) \
                        and Path(path.value).name.lower() == own_exe:
                    found.append(hwnd)
                kernel32.CloseHandle(process)
        return True

    user32.EnumWindows(per_window, 0)
    return found[0] if found else None


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def mix(start: tuple, end: tuple, t: float) -> tuple:
    """Mix colors (hex): t=0 -> start, t=1 -> end."""
    return tuple("#%02x%02x%02x" % tuple(round(a + (b - a) * t) for a, b in zip(_rgb(c1), _rgb(c2)))
                 for c1, c2 in zip(start, end))


def color_title_bar(hwnd: int, colors: tuple = BAR_LIGHT, dark: bool | None = None) -> None:
    """Title bar in the color of the app's top bar (Windows 11; on older Windows nothing happens)."""
    dwm = ctypes.windll.dwmapi
    if dark is not None:  # light or dark buttons (minimize, close, ...)
        value = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), 20, ctypes.byref(value), 4)  # USE_IMMERSIVE_DARK_MODE
    for attribute, color in zip((35, 36, 34), colors):  # caption, text, border
        r, g, b = _rgb(color)
        value = ctypes.c_int(b << 16 | g << 8 | r)  # COLORREF 0x00bbggrr
        dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), attribute, ctypes.byref(value), 4)


def windows_dark() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def starts_dark(theme: str) -> bool:
    """First guess for the window color (the page corrects this right away, also for the 'sun' setting)."""
    return theme == "dark" or (theme == "system" and windows_dark())


class Api:
    """Functions the pages can call through window.pywebview.api."""

    def __init__(self, base_url: str, dark: bool = False):
        self.base_url = base_url
        self.hwnd = None
        self._colors = BAR_DARK if dark else BAR_LIGHT
        self._lock = threading.Lock()

    def title_bar(self, dark: bool, smooth: bool = False) -> None:
        """Let the title bar follow the theme; when switching, smoothly in 0.6 s."""
        if self.hwnd:
            threading.Thread(target=self._color, args=(bool(dark), bool(smooth)), daemon=True).start()

    def _color(self, dark: bool, smooth: bool) -> None:
        target = BAR_DARK if dark else BAR_LIGHT
        with self._lock:
            steps = 20 if smooth and target != self._colors else 1
            for i in range(1, steps + 1):
                color_title_bar(self.hwnd, mix(self._colors, target, i / steps), dark if i == steps else None)
                if steps > 1:
                    time.sleep(0.6 / steps)
            self._colors = target

    def choose_folder(self) -> str | None:
        """Choose a folder for the backups (Windows folder picker)."""
        import webview
        choice = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER)
        return (choice if isinstance(choice, str) else choice[0]) if choice else None

    def save(self, path: str, name: str) -> str | None:
        """Save the sale PDF through a Windows 'Save as' dialog. Returns the chosen path (or None)."""
        import re
        import urllib.request
        import webview
        if not re.fullmatch(r"/vehicles/\d+/export\.pdf(\?[\w=&]*)?", path):
            raise ValueError("Invalid export")
        choice = webview.windows[0].create_file_dialog(
            webview.FileDialog.SAVE, directory=str(Path.home() / "Downloads"), save_filename=name,
            file_types=("PDF bestand (*.pdf)",))  # pywebview: only letters/spaces in the description
        if not choice:
            return None  # cancelled
        target = Path(choice if isinstance(choice, str) else choice[0])
        # bandit B310 is fine here: always http://127.0.0.1 + a path checked above
        with urllib.request.urlopen(self.base_url + path, timeout=120) as r:  # nosec B310
            target.write_bytes(r.read())
        return str(target)


def start_server(port: int):
    import uvicorn
    from app.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(300):
        if server.started:
            return server, thread
        if not thread.is_alive():
            break
        time.sleep(0.05)
    raise RuntimeError("De interne server van Garage kon niet starten.")


def show_notification(title: str, text: str) -> bool:
    """Windows notification (toast) via PowerShell; clicking it opens Garage."""
    import base64
    import json
    import subprocess
    script = (HERE / "_internal" if FROZEN else HERE) / "app" / "notification.ps1"
    data = base64.b64encode(json.dumps({"title": title, "text": text}).encode()).decode()
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                        "-Data", data, "-AppId", APP_ID], timeout=60, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        logging.warning("Showing notification failed: %s", r.stderr.decode("utf-8", "replace")[:300])
    return r.returncode == 0


def check() -> None:
    """'Garage.exe --check' (when signing in to Windows): notification if maintenance is due. No window."""
    data = data_folder()
    if not (data / "onderhoud.db").exists():
        return
    os.environ[DATA_ENV] = str(data)
    log_to(data)
    sys.path.insert(0, str(HERE))
    try:
        from app import db, extra
        db.init()
        lines = extra.check_notifications()
        if extra.may_notify(lines):
            texts = [t for _, t in lines]
            text = "\n".join(texts[:3]) + (f"\n…en nog {len(texts) - 3}" if len(texts) > 3 else "")
            if show_notification("Garage: er is iets te doen", text):
                extra.notified(lines)
                logging.info("Reminder shown (%d items)", len(lines))
    except Exception:  # noqa: BLE001 - never show an error window in the background
        logging.exception("Check failed")


def self_test(report: Path | None = None) -> bool:
    """'Garage.exe --self-test [report.txt]' (build.bat): starts the server with empty data in a temporary
    folder, goes through the main screens (including those with PDFs, images and OCR) and stops. Catches errors
    that only occur in the built exe, such as a missing module or a left-out Pillow part.
    No window, and the real data is not touched."""
    import tempfile
    import traceback
    sys.path.insert(0, str(HERE))
    lines = []
    with tempfile.TemporaryDirectory(prefix="garage-selftest-", ignore_cleanup_errors=True) as tmp:
        try:
            from app import db
            old_dir, db.DATA_DIR = db.DATA_DIR, Path(tmp).resolve()  # like db.DATA_DIR itself (no 8.3 path)
            try:
                db.init()
                with db.connect() as conn:
                    db.set_setting(conn, "backup_auto", "off")  # never a backup to the real backup folder
                    oil = next(t["id"] for t in db.types_for(conn, "Motorfiets") if t["name"] == "Olie vervangen")
                server, thread = start_server(free_port())
                try:
                    _self_test_steps(f"http://127.0.0.1:{server.config.port}", oil, lines)
                finally:
                    server.should_exit = True
                    thread.join(timeout=10)
            finally:
                db.DATA_DIR = old_dir
            passed = True
        except Exception:  # noqa: BLE001 - every error belongs in the report
            lines.append("FAIL " + traceback.format_exc())
            passed = False
    if report:
        report.write_text("\n".join(lines + ["Self-test " + ("passed" if passed else "FAILED")]), encoding="utf-8")
    return passed


def _self_test_steps(base: str, oil: int, lines: list[str]) -> None:
    import io
    import json
    import httpx
    import pymupdf
    from PIL import Image

    def step(description: str, r: httpx.Response, status: int = 200) -> httpx.Response:
        if r.status_code != status:
            raise RuntimeError(f"{description}: status {r.status_code}, expected {status}\n{r.text[:1000]}")
        lines.append(f"OK   {description}")
        return r

    from app import crypto
    key = crypto.new_key()  # catches a missing cryptography (cffi) or crypt32 call in the exe
    if crypto.decrypt(crypto.unprotect(crypto.protect(key)), crypto.encrypt(key, b"ok", "self-test"), "self-test") != b"ok":
        raise RuntimeError("encryption round trip failed")
    lines.append("OK   encryption (DPAPI + AES-GCM)")

    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Invoice self-test")
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, "JPEG")
    jpg = buf.getvalue()
    invoices = [("invoices", ("factuur.pdf", pdf.tobytes(), "application/pdf")),
                ("invoices", ("factuur.jpg", jpg, "image/jpeg"))]

    with httpx.Client(base_url=base, timeout=120) as c:
        for path in ("/health", "/", "/settings", "/vehicles/new", "/static/app.css"):
            step(f"GET {path}", c.get(path))
        v = step("add vehicle", c.post("/vehicles", data={
            "license_plate": "ZT-01-ST", "vehicle_type": "Motorfiets", "make": "Test"}), 303).headers["location"]
        for sub in ("", "/costs", "/documents", "/specs", "/storage", "/intervals"):
            step(f"GET {v}{sub}", c.get(v + sub))
        step("read invoice (OCR)", c.post(f"{v}/read-invoice", files=invoices))
        redact = step("save service with invoices", c.post(f"{v}/services/new", files=invoices, data={
            "date": "2026-01-15", "odometer_km": "12000", "types": [str(oil)]}), 303).headers["location"]
        for _ in invoices:
            invoice = step("to the next invoice", c.get(redact), 303).headers["location"]
            step(f"GET {invoice}", c.get(invoice))
            step("page as image", c.get(invoice.replace("/redact", "/page/0.png")))
            box = [{"page": 0, "x0": 0.1, "y0": 0.1, "x1": 0.4, "y1": 0.2}]
            step("redact", c.post(invoice, data={"redactions": json.dumps(box)}), 303)
        if not step("sale PDF", c.get(f"{v}/export.pdf")).content.startswith(b"%PDF"):
            raise RuntimeError("sale PDF is not a PDF")
        step("save own photo", c.post(f"{v}/image/upload", files={"photo": ("foto.jpg", jpg, "image/jpeg")}), 303)
        step("show photo", c.get(f"{v}/image.jpg"))


def main() -> None:
    if "--check" in sys.argv or "--controle" in sys.argv:  # --controle: old name, still in the Run key of 1.3 installs
        check()
        return
    if "--self-test" in sys.argv:
        rest = sys.argv[sys.argv.index("--self-test") + 1:]
        sys.exit(0 if self_test(Path(rest[0]) if rest else None) else 1)
    if already_running():
        return
    data = data_folder()
    data.mkdir(parents=True, exist_ok=True)
    os.environ[DATA_ENV] = str(data)  # before importing the app (db.DATA_DIR)
    log_to(data)
    sys.path.insert(0, str(HERE))
    try:
        import webview
        from app import backup, db
        backup.clean_up()
        db.init()  # already here, so an error (e.g. data of a newer version) ends up in the message
        server, thread = start_server(free_port())
        with db.connect() as conn:
            dark = starts_dark(db.setting(conn, "theme", "system"))
        base = f"http://127.0.0.1:{server.config.port}"
        api = Api(base, dark)
        window = webview.create_window(TITLE, base, js_api=api, width=1280, height=860, min_size=(420, 600),
                                       background_color="#0e1014" if dark else "#f5f6f8", text_select=True)

        def on_shown():
            try:
                api.hwnd = int(window.native.Handle.ToInt64())
                color_title_bar(api.hwnd, BAR_DARK if dark else BAR_LIGHT, dark)
            except Exception:  # noqa: BLE001 - purely cosmetic, never let the app fail
                logging.exception("Coloring the title bar failed")

        window.events.shown += on_shown
        webview.start(gui="edgechromium")  # blocks until the window is closed
        server.should_exit = True
        thread.join(timeout=5)
    except Exception as e:
        logging.exception("Garage could not start")
        tip = "\n\nControleer of 'Microsoft Edge WebView2 Runtime' is geïnstalleerd." if "webview" in str(e).lower() else ""
        message_box(f"Garage kon niet starten:\n{e}{tip}\n\nDetails: {data / 'app.log'}")


if __name__ == "__main__":
    main()
