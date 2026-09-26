import json
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.database.workspace_repository import WorkspaceBudget
from app.services.credit_budget import BudgetExceeded
from app.services.eligibility import evaluate
from app.services.provider_vault import ProviderVault
from app.services.workspace_profile import matching_profile
from app.services.workspace_store import WorkspaceError
from app.workspace_worker import work_once
from tests.test_eligibility import job
from tests.test_workspaces import BASE, authenticate, setup


def profile():
    return {
        "schema_version": 1,
        "education": [{"degree": "BS Data Science", "graduation_year": 2027}],
        "skills": ["Python", "Flask", "SQL", "REST APIs"],
        "projects": [{"name": "API", "technologies": ["Python"]}],
        "experience": [{"summary": "Built Python Flask SQL applications"}],
        "preferences_to_confirm": {
            "desired_roles": ["Backend Intern"],
            "countries_with_work_authorization": ["India"],
            "accept_undisclosed_compensation": True,
        },
    }


def test_vault_ciphertext_is_bound_to_owner_and_provider():
    vault = ProviderVault(Fernet.generate_key().decode())
    ciphertext = vault.seal("alice", "firecrawl", "private-api-key")
    assert "private-api-key" not in ciphertext
    assert vault.open("alice", "firecrawl", ciphertext) == "private-api-key"
    for owner, provider in [("bob", "firecrawl"), ("alice", "gemini")]:
        with pytest.raises(ValueError):
            vault.open(owner, provider, ciphertext)


def test_profile_does_not_inherit_owner_preferences():
    data = profile()
    data["preferences_to_confirm"]["desired_roles"] = ["Data Analyst Intern"]
    converted = matching_profile(data)
    assert converted["primary_roles"] == ["Data Analyst Intern"]
    assert "Outside your confirmed desired roles" in evaluate(job(), converted).reasons
    data["preferences_to_confirm"]["countries_with_work_authorization"] = []
    with pytest.raises(ValueError, match="work authorization"):
        matching_profile(data)


def test_worker_disabled_prevents_queueing():
    app, settings, store = setup()
    client = app.test_client()
    csrf = authenticate(client, settings, "alice")
    assert client.post("/discovery", base_url=BASE, data={"csrf": csrf, "kind": "search"}).status_code == 503


def test_settings_can_be_saved_before_worker_activation():
    app, settings, store = setup()
    saved = []
    store.save_discovery_settings = lambda token, uid, limit, enabled: saved.append((uid, limit, enabled))
    client = app.test_client()
    csrf = authenticate(client, settings, "alice")
    response = client.post(
        "/discovery",
        base_url=BASE,
        data={
            "csrf": csrf,
            "action": "settings",
            "weekly_limit": "100",
            "scheduled": "yes",
        },
    )
    assert response.status_code == 303
    assert saved == [("alice", 100, True)]


def test_budget_fail_closed():
    class Broken:
        def call(self, *args, **kwargs):
            raise WorkspaceError("offline")

    with pytest.raises(BudgetExceeded):
        WorkspaceBudget(Broken(), {"id": "task", "claim_token": "claim"}, 100).reserve("scrape", 1)


def test_worker_import_uses_only_claimed_users_key_and_persists(monkeypatch):
    secret = Fernet.generate_key().decode()
    task = {
        "id": "task-id",
        "user_id": "alice",
        "claim_token": "lease",
        "kind": "import",
        "job_url": "https://jobs.lever.co/acme/123",
    }
    encrypted = ProviderVault(secret).seal("alice", "firecrawl", "alice-firecrawl-key")

    class Store:
        def __init__(self):
            self.writes = []

        def profile(self, token, owner):
            assert token is None and owner == "alice"
            return profile()

        def discovery_settings(self, token, owner):
            return {"weekly_limit": 100}

        def call(self, method, path, **kwargs):
            if path.endswith("/claim_discovery"):
                return [task]
            if method == "GET":
                assert kwargs["params"]["user_id"] == "eq.alice"
                if path.endswith("provider_connections"):
                    return [{"ciphertext": encrypted}]
                if path.endswith("credit_reservations"):
                    return [{"reserved": 1, "reported": 1}]
                return []
            self.writes.append((path, kwargs))
            if path.endswith("reserve_discovery_credit"):
                assert kwargs["json"]["p_task"] == "task-id"
                assert kwargs["json"]["p_claim"] == "lease"
                return "reservation"
            if path.endswith("opportunities"):
                assert kwargs["json"]["user_id"] == "alice"
                return [
                    {
                        **kwargs["json"],
                        "id": "job-id",
                        "status": "NEW",
                        "notes": "",
                        "created_at": "2026-09-26T00:00:00+00:00",
                        "updated_at": "2026-09-26T00:00:00+00:00",
                    }
                ]
            if path.endswith("discovery_tasks"):
                assert kwargs["params"]["user_id"] == "eq.alice"
                return [kwargs["json"]]

    class Firecrawl:
        def __init__(self, key, budget):
            assert key == "alice-firecrawl-key"
            self.budget = budget

        def scrape(self, url):
            assert url == task["job_url"]
            reservation = self.budget.reserve("scrape", 1)
            self.budget.finish(reservation, 1)
            return {}

    monkeypatch.setattr("app.workspace_worker.Firecrawl", Firecrawl)
    monkeypatch.setattr(
        "app.services.pipeline.extract",
        lambda *args: job(
            description="Build Python Flask SQL REST APIs for undergraduate students. " * 4,
            graduation_requirement="2027 graduates",
            evidence={"public_api": True, "active": True},
        ),
    )
    settings = Settings(supabase_worker_key="worker-secret", provider_encryption_key=secret)
    store = Store()
    result = work_once(settings, store)
    assert result["state"] == "COMPLETED"
    assert any(path.endswith("opportunities") for path, _ in store.writes)
    assert "alice-firecrawl-key" not in json.dumps(store.writes)


def test_connection_form_never_returns_saved_ciphertext(monkeypatch):
    from app.workspaces import create_workspace_app

    app, settings, store = setup()
    settings = replace(settings, provider_encryption_key=Fernet.generate_key().decode())
    saved = []
    store.connections = lambda *args: [{"provider": "firecrawl", "last_four": "1234"}]
    store.save_connection = lambda *args: saved.append(args)
    monkeypatch.setattr("app.services.firecrawl_service.Firecrawl.account_credits", lambda _: 200)
    client = create_workspace_app(settings, store).test_client()
    csrf = authenticate(client, settings, "alice")
    response = client.post(
        "/connections", base_url=BASE, data={"csrf": csrf, "api_key": "fc-private-key-1234"}
    )
    assert response.status_code == 200
    assert b"fc-private-key-1234" not in response.data
    assert saved[0][1] == "alice" and "fc-private-key-1234" not in saved[0][2]
