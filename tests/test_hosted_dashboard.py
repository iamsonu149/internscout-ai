from datetime import timedelta

import pytest
from bs4 import BeautifulSoup

from app.config import Settings
from app.dashboard import create_app

BASE = "https://internscout-test.vercel.app"


class Feed:
    calls = 0

    def read(self, force=False):
        self.calls += 1
        return {"jobs": [], "rejected": [], "synced_at": "2026-09-25T00:00:00Z", "error": None}


def config(tmp_path):
    return Settings(
        dashboard_mode="hosted",
        sheet_id="test",
        database_path=str(tmp_path / "absent.db"),
        dashboard_password="a" * 32,
        dashboard_secret_key="b" * 64,
    )


def csrf(client):
    response = client.get("/login", base_url=BASE)
    return BeautifulSoup(response.data, "html.parser").select_one('input[name="csrf"]')["value"]


def login(client, settings, remember=True):
    values = {
        "csrf": csrf(client),
        "username": settings.dashboard_username,
        "password": settings.dashboard_password,
    }
    if remember:
        values["remember"] = "on"
    return client.post("/login", base_url=BASE, data=values)


def test_hosted_fails_closed_without_secrets():
    with pytest.raises(ValueError):
        create_app(Settings(dashboard_mode="hosted", sheet_id="test"))


def test_login_required_before_sheet_access_and_no_browser_popup(tmp_path):
    settings, feed = config(tmp_path), Feed()
    client = create_app(settings, sheet_feed=feed).test_client()
    response = client.get("/", base_url=BASE)
    assert response.status_code == 303 and response.headers["Location"] == "/login"
    assert "WWW-Authenticate" not in response.headers and feed.calls == 0
    page = client.get("/login", base_url=BASE)
    assert page.status_code == 200 and b"Welcome back." in page.data
    assert login(client, settings).status_code == 303
    response = client.get("/", base_url=BASE)
    assert response.status_code == 200 and feed.calls == 1
    assert b"Sign out" in response.data
    assert "Secure" in response.headers["Set-Cookie"]
    assert not (tmp_path / "absent.db").exists()
    assert client.get("/", base_url="https://evil.example").status_code == 400


def test_bad_password_and_missing_csrf_deny_access(tmp_path):
    settings, feed = config(tmp_path), Feed()
    client = create_app(settings, sheet_feed=feed).test_client()
    response = client.post(
        "/login", base_url=BASE, data={"csrf": csrf(client), "username": "internscout", "password": "wrong"}
    )
    assert response.status_code == 401 and b"correct. Please try again" in response.data
    assert "WWW-Authenticate" not in response.headers
    assert settings.dashboard_password.encode() not in response.data
    response = client.post(
        "/login", base_url=BASE, data={"username": "internscout", "password": settings.dashboard_password}
    )
    assert response.status_code == 400
    assert client.get("/", base_url=BASE).status_code == 303 and feed.calls == 0


def test_remembered_session_survives_redeploy_and_rotation_revokes_it(tmp_path):
    settings = config(tmp_path)
    app = create_app(settings, sheet_feed=Feed())
    assert app.permanent_session_lifetime == timedelta(days=365)
    client = app.test_client()
    response = login(client, settings)
    assert "Expires=" in response.headers["Set-Cookie"]
    cookie = client.get_cookie("session", domain="internscout-test.vercel.app")
    deployed_again = create_app(settings, sheet_feed=Feed()).test_client()
    deployed_again.set_cookie("session", cookie.value, domain="internscout-test.vercel.app")
    assert deployed_again.get("/", base_url=BASE).status_code == 200
    settings.dashboard_password = "c" * 32
    rotated = create_app(settings, sheet_feed=Feed()).test_client()
    rotated.set_cookie("session", cookie.value, domain="internscout-test.vercel.app")
    assert rotated.get("/", base_url=BASE).status_code == 303


def test_nonremembered_session_and_logout(tmp_path):
    settings = config(tmp_path)
    client = create_app(settings, sheet_feed=Feed()).test_client()
    assert "Expires=" not in login(client, settings, remember=False).headers["Set-Cookie"]
    assert client.post("/logout", base_url=BASE).status_code == 403
    page = client.get("/", base_url=BASE)
    token = BeautifulSoup(page.data, "html.parser").select_one('input[name="csrf"]')["value"]
    assert client.post("/logout", base_url=BASE, data={"csrf": token}).status_code == 303
    assert client.get("/", base_url=BASE).status_code == 303
