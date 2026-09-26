import base64
import hashlib
import json
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet

from app.services.workspace_store import WorkspaceStore
from app.workspaces import COOKIE, create_workspace_app
from tests.test_workspaces import BASE, authenticate, setup


def google_setup():
    _, settings, store = setup()
    settings = replace(settings, workspace_google_enabled="on", workspace_origin=BASE)
    store.exchanges = []

    def exchange(code, verifier):
        store.exchanges.append((code, verifier))
        return {"access_token": "alice", "refresh_token": "refresh-alice", "expires_in": 3600}

    store.exchange_code = exchange
    return create_workspace_app(settings, store), settings, store


def start(client, next_path="/profile"):
    page = client.get("/login", base_url=BASE)
    csrf = BeautifulSoup(page.data, "html.parser").select_one("input[name=csrf]")["value"]
    response = client.post("/auth/google", base_url=BASE, data={"csrf": csrf, "next": next_path})
    return response


def test_google_pkce_callback_and_replay():
    app, settings, store = google_setup()
    client = app.test_client()
    response = start(client)
    assert response.status_code == 303
    target = urlsplit(response.location)
    assert target.hostname == "example.supabase.co" and target.path == "/auth/v1/authorize"
    query = parse_qs(target.query)
    assert query["provider"] == ["google"] and query["code_challenge_method"] == ["s256"]
    with client.session_transaction(base_url=BASE) as session:
        flow = json.loads(Fernet(settings.workspace_cookie_key.encode()).decrypt(session["oauth"].encode()))
        assert flow["verifier"] not in session["oauth"]
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(flow["verifier"].encode()).digest()).rstrip(b"=").decode()
    )
    assert query["code_challenge"] == [expected]
    callback = query["redirect_to"][0] + "&code=provider-code"
    result = client.get(callback)
    assert result.status_code == 303 and result.location == "/profile"
    assert store.exchanges == [("provider-code", flow["verifier"])]
    cookie = client.get_cookie(COOKIE, domain="workspace.vercel.app")
    assert cookie and cookie.secure and cookie.http_only
    assert "refresh-alice" not in cookie.value
    assert client.get(callback).status_code == 400
    assert len(store.exchanges) == 1


@pytest.mark.parametrize("failure", ["state", "expired", "other-browser", "provider-error", "missing-code"])
def test_google_invalid_flow_never_exchanges_code(failure):
    app, settings, store = google_setup()
    client = app.test_client()
    response = start(client)
    callback = parse_qs(urlsplit(response.location).query)["redirect_to"][0] + "&code=provider-code"
    if failure == "state":
        callback = BASE + "/auth/callback?flow=wrong&code=provider-code"
    elif failure == "other-browser":
        client = app.test_client()
    elif failure == "expired":
        cipher = Fernet(settings.workspace_cookie_key.encode())
        with client.session_transaction(base_url=BASE) as session:
            raw = cipher.decrypt(session["oauth"].encode())
            session["oauth"] = cipher.encrypt_at_time(raw, int(time.time()) - 601).decode()
    elif failure == "provider-error":
        callback += "&error=access_denied&error_description=do-not-reflect"
    else:
        callback = callback.replace("&code=provider-code", "")
    result = client.get(callback)
    assert result.status_code == 400 and store.exchanges == []
    assert b"do-not-reflect" not in result.data
    assert not client.get_cookie(COOKIE, domain="workspace.vercel.app")


def test_google_csrf_fixed_origin_and_closed_registration():
    app, settings, store = google_setup()
    client = app.test_client()
    assert client.post("/auth/google", base_url=BASE).status_code == 400
    response = start(client, "https://evil.example")
    callback = parse_qs(urlsplit(response.location).query)["redirect_to"][0]
    assert callback.startswith(BASE + "/auth/callback?")
    assert client.get(callback + "&code=test").location == "/"
    app, _, _ = setup()
    client = app.test_client()
    assert b"not open yet" in client.get("/signup", base_url=BASE).data
    assert start(client).status_code == 503
    with pytest.raises(ValueError):
        create_workspace_app(replace(settings, workspace_origin="http://evil.example"), store)


def test_navigation_after_login_and_return_to_requested_page():
    app, settings, store = setup()
    store.connections = lambda token, uid: []
    store.discovery_settings = lambda token, uid: {"weekly_limit": 100, "schedule_enabled": False}
    store.tasks = lambda token, uid: []
    client = app.test_client()
    assert "next=/connections" in client.get("/connections", base_url=BASE).location
    login = client.get("/login?next=/connections", base_url=BASE)
    assert b'href="/connections"' not in login.data
    csrf = BeautifulSoup(login.data, "html.parser").select_one("input[name=csrf]")["value"]
    result = client.post(
        "/login",
        base_url=BASE,
        data={
            "csrf": csrf,
            "email": "alice@example.com",
            "password": "private-test-password",
            "next": "/connections",
        },
    )
    assert result.location == "/connections"
    for path in ("/", "/profile", "/connections", "/discovery"):
        page = client.get(path, base_url=BASE)
        assert page.status_code == 200
        assert b'href="/connections"' in page.data
    assert client.get("/login", base_url=BASE).location == "/"
    assert client.get("/signup", base_url=BASE).location == "/"


def test_expired_cookie_can_be_replaced_by_fresh_password_login():
    app, settings, store = setup()
    client = app.test_client()
    csrf = authenticate(client, settings, "alice")
    client.set_cookie(COOKIE, "invalid", domain="workspace.vercel.app")
    result = client.post(
        "/login",
        base_url=BASE,
        data={
            "csrf": csrf,
            "email": "alice@example.com",
            "password": "private-test-password",
        },
    )
    assert result.status_code == 303
    assert client.get("/", base_url=BASE).status_code == 200


def test_pkce_adapter_never_uses_service_credentials():
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"access_token": "access"})

    store = WorkspaceStore(
        "https://example.supabase.co",
        "sb_publishable_test",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    store.exchange_code("auth-code", "private-verifier")
    assert seen[0].url.params["grant_type"] == "pkce"
    assert json.loads(seen[0].content) == {"auth_code": "auth-code", "code_verifier": "private-verifier"}
    assert seen[0].headers["apikey"] == "sb_publishable_test"


def test_external_exchange_error_is_actionable_without_reflecting_code():
    app, settings, store = google_setup()
    client = app.test_client()
    response = client.get(
        "/auth/callback?error=server_error&error_code=unexpected_failure"
        "&error_description=Unable+to+exchange+external+code:+private-google-code",
        base_url=BASE,
    )
    assert response.status_code == 400
    assert b"administrator needs to check" in response.data
    assert b"private-google-code" not in response.data
    assert not store.exchanges
