import json
import time
from dataclasses import replace

import httpx
import pytest
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet

from app.config import Settings
from app.services.resume_profile import EMPTY_PROFILE, parse_profile
from app.services.workspace_store import WorkspaceError, WorkspaceStore
from app.workspaces import COOKIE, create_workspace_app

BASE = "https://workspace.vercel.app"


class Store:
    def __init__(self):
        self.profiles = {}
        self.calls = []

    def user(self, token):
        self.calls.append(("user", token))
        return {"id": token, "email": token + "@example.com"}

    def profile(self, token, uid):
        assert token == uid
        return self.profiles.get(uid, {})

    def save_profile(self, token, uid, document):
        assert token == uid
        self.profiles[uid] = document

    def opportunities(self, token, uid, offset):
        assert token == uid
        return []

    def track(self, token, uid, job_id, status, notes):
        self.calls.append(("track", token, uid, job_id))
        return []

    def send_code(self, email):
        self.calls.append(("otp", email))

    def verify_code(self, email, code):
        return {"access_token": "alice", "refresh_token": "refresh-alice", "expires_in": 3600}

    def refresh(self, token):
        self.calls.append(("refresh", token))
        return {"access_token": "alice", "refresh_token": "new-refresh", "expires_in": 3600}

    def logout(self, token):
        self.calls.append(("logout", token))


def setup():
    settings = Settings(
        supabase_url="https://example.supabase.co",
        supabase_public_key="public-key",
        workspace_cookie_key=Fernet.generate_key().decode(),
        dashboard_secret_key="x" * 40,
    )
    store = Store()
    return create_workspace_app(settings, store), settings, store


def authenticate(client, settings, uid, expires=None):
    value = (
        Fernet(settings.workspace_cookie_key.encode())
        .encrypt(
            json.dumps(
                {
                    "access_token": uid,
                    "refresh_token": "refresh-" + uid,
                    "expires_at": expires or time.time() + 3600,
                }
            ).encode()
        )
        .decode()
    )
    client.set_cookie(COOKIE, value, domain="workspace.vercel.app")
    client.get("/", base_url=BASE)
    with client.session_transaction(base_url=BASE) as session:
        return session["csrf"]


def test_anonymous_and_forged_sessions_do_not_read_data():
    app, settings, store = setup()
    client = app.test_client()
    assert client.get("/profile", base_url=BASE).status_code == 303
    client.set_cookie(COOKIE, "forged", domain="workspace.vercel.app")
    assert client.get("/", base_url=BASE).status_code == 303
    assert store.calls == []


def test_review_then_save_and_user_isolation():
    app, settings, store = setup()
    alice, bob = app.test_client(), app.test_client()
    csrf = authenticate(alice, settings, "alice")
    authenticate(bob, settings, "bob")
    document = {**EMPTY_PROFILE, "skills": ["Alice-private-skill"]}
    form = {"csrf": csrf, "document": json.dumps(document), "action": "review"}
    assert alice.post("/profile", base_url=BASE, data=form).status_code == 200
    assert not store.profiles
    assert alice.post("/profile", base_url=BASE, data={**form, "action": "save"}).status_code == 200
    assert not store.profiles
    response = alice.post(
        "/profile", base_url=BASE, data={**form, "action": "save", "confirmed": "yes", "user_id": "bob"}
    )
    assert response.status_code == 303 and "alice" in store.profiles and "bob" not in store.profiles
    assert b"Alice-private-skill" not in bob.get("/profile", base_url=BASE).data
    assert b"Alice-private-skill" in alice.get("/profile", base_url=BASE).data


def test_csrf_and_cross_user_tracking():
    app, settings, store = setup()
    client = app.test_client()
    csrf = authenticate(client, settings, "alice")
    assert client.post("/profile", base_url=BASE, data={"csrf": "wrong"}).status_code == 400
    response = client.post(
        "/jobs/00000000-0000-0000-0000-000000000002/tracking",
        base_url=BASE,
        data={"csrf": csrf, "status": "SAVED", "user_id": "bob"},
    )
    assert response.status_code == 404
    assert store.calls[-1][1:3] == ("alice", "alice")


def test_otp_login_tokens_encrypted_and_refresh():
    app, settings, store = setup()
    client = app.test_client()
    soup = BeautifulSoup(client.get("/login", base_url=BASE).data, "html.parser")
    csrf = soup.select_one("input[name=csrf]")["value"]
    client.post("/login", base_url=BASE, data={"csrf": csrf, "email": "alice@example.com"})
    response = client.post("/login", base_url=BASE, data={"csrf": csrf, "action": "verify", "code": "123456"})
    assert response.status_code == 303
    cookie = response.headers.getlist("Set-Cookie")[0]
    assert "refresh-alice" not in cookie and "HttpOnly" in cookie and "Secure" in cookie
    authenticate(client, settings, "alice", expires=time.time() - 60)
    assert ("refresh", "refresh-alice") in store.calls


def test_manual_profile_review():
    app, settings, store = setup()
    client = app.test_client()
    csrf = authenticate(client, settings, "alice")
    response = client.post(
        "/profile",
        base_url=BASE,
        data={
            "csrf": csrf,
            "action": "manual",
            "skills": "Python, SQL",
            "year": "2027",
            "graduation": "expected",
            "full_time": "yes",
        },
    )
    assert response.status_code == 200 and b"Python" in response.data
    assert not store.profiles


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"schema_version": 1, "skills": "Python"},
        {"schema_version": 1, "skills": [], "user_id": "victim"},
        {"schema_version": 1, "skills": [], "portfolio_url": "javascript:alert(1)"},
        {"schema_version": 1, "skills": [], "education": [{"graduation_year": 9999}]},
    ],
)
def test_untrusted_profile_rejected(document):
    with pytest.raises(ValueError):
        parse_profile(json.dumps(document))


def test_duplicate_json_fields_and_oversize_rejected():
    for raw in ['{"schema_version":1,"skills":[],"skills":["override"]}', "x" * 50001]:
        with pytest.raises(ValueError):
            parse_profile(raw)


def test_nonfinite_values_rejected_and_html_escaped():
    with pytest.raises(ValueError):
        parse_profile('{"schema_version":1,"skills":[],"current_city":NaN}')
    app, settings, _ = setup()
    client = app.test_client()
    csrf = authenticate(client, settings, "alice")
    response = client.post(
        "/profile",
        base_url=BASE,
        data={
            "csrf": csrf,
            "document": json.dumps({"schema_version": 1, "skills": ["<script>alert(1)</script>"]}),
        },
    )
    assert response.status_code == 200 and b"<script>alert(1)</script>" not in response.data


def test_postgrest_always_uses_user_token_and_owner_filter():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    store = WorkspaceStore(
        "https://example.supabase.co", "public-key", httpx.Client(transport=httpx.MockTransport(handler))
    )
    store.opportunities("alice-jwt", "alice-id")
    store.track("bob-jwt", "bob-id", "job-id", "SAVED", "")
    assert requests[0].headers["Authorization"] == "Bearer alice-jwt"
    assert requests[0].url.params["user_id"] == "eq.alice-id"
    assert requests[1].headers["Authorization"] == "Bearer bob-jwt"
    assert requests[1].url.params["user_id"] == "eq.bob-id"


def test_provider_error_never_exposes_response_secrets():
    store = WorkspaceStore(
        "https://example.supabase.co",
        "key",
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500, text="SECRET"))),
    )
    with pytest.raises(WorkspaceError, match="Request could not") as error:
        store.user("token")
    assert "SECRET" not in str(error.value)


def test_invalid_configuration_does_not_fallback_to_personal_data():
    _, settings, _ = setup()
    with pytest.raises(ValueError):
        create_workspace_app(replace(settings, supabase_url="https://evil.example"))
