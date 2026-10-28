"""S4: only the window that Garage opened may use the server, not another Windows user on the same pc."""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from tests.test_app import add_vehicle, client  # noqa: F401


@pytest.fixture
def stranger(client):
    """Someone else on this pc: same server, no session cookie (the app itself is already running)."""
    with TestClient(app) as other:
        app.state.backup_thread.join()
        yield other


@pytest.mark.parametrize("method, path", [
    ("get", "/"), ("get", "/settings"), ("get", "/vehicles/1"), ("get", "/invoices/1"), ("get", "/documents/1"),
    ("get", "/vehicles/1/export.pdf"), ("post", "/vehicles/1/delete"), ("post", "/backup/now"), ("get", "/recover"),
])
def test_without_session_everything_is_refused(client, stranger, method, path):
    add_vehicle(client)
    r = getattr(stranger, method)(path, follow_redirects=False)
    assert r.status_code == 403 and "snelkoppeling" in r.text
    assert client.get("/vehicles/1").status_code == 200  # nothing changed for the real window


def test_wrong_or_missing_token_gives_no_session(stranger):
    for url in ("/start", "/start?token=guess", f"/start?token={main.SESSION_TOKEN[:-1]}", "/start?token=%C3%A9"):
        r = stranger.get(url, follow_redirects=False)
        assert r.status_code == 403 and "set-cookie" not in r.headers
    assert stranger.get("/", follow_redirects=False).status_code == 403
    stranger.cookies.set(main.SESSION_COOKIE, "guess")
    assert stranger.get("/", follow_redirects=False).status_code == 403


def test_start_url_sets_a_strict_http_only_cookie_and_leaves_the_address_bar(stranger):
    r = stranger.get(main.start_url("http://testserver"), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age" not in cookie  # gone when Garage closes
    assert stranger.get("/").status_code == 200


def test_what_works_without_session_holds_nothing_personal(stranger):
    assert stranger.get("/health").json() == {"app": "garage"}
    assert stranger.get("/static/app.css").status_code == 200
    assert stranger.get("/favicon.ico").status_code == 200


def test_token_cannot_be_guessed():
    assert len(main.SESSION_TOKEN) >= 40  # 32 random bytes, url-safe: not to guess or try out
