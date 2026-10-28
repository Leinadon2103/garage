"""Development (start.bat): runs Garage on http://127.0.0.1:8000 and opens it in the browser.

The server only answers with the session cookie (S4), and only this process knows the token of this start: so
open the browser from here, with the start URL. Stop with Ctrl+C. The installed app uses Garage.pyw instead.
"""
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402 - after the project folder is on the path

from app.main import app, start_url  # noqa: E402

BASE = "http://127.0.0.1:8000"


def open_when_ready(server: uvicorn.Server) -> None:
    while not server.started:
        time.sleep(0.1)
    webbrowser.open(start_url(BASE))
    print(f"Garage draait op {BASE} (stoppen met Ctrl+C)")


if __name__ == "__main__":
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000))
    threading.Thread(target=open_when_ready, args=(server,), daemon=True).start()
    server.run()
