import json
from dataclasses import replace

import pytest

from app.services.job_preview import inspect_import, validate_import_url
from app.workspaces import create_workspace_app
from tests.test_eligibility import job
from tests.test_workspaces import BASE, authenticate, setup


def test_import_policy_and_spoofed_company_stays_private(monkeypatch, tmp_path):
    source = tmp_path / "sources.json"
    source.write_text(
        json.dumps(
            {"official_domains": {}, "boards": [{"type": "lever", "slug": "real-company", "company": "Acme"}]}
        )
    )
    monkeypatch.setattr(
        "app.services.job_preview.socket.getaddrinfo", lambda *a, **kw: [(0, 0, 0, "", ("8.8.8.8", 443))]
    )

    class Fetch:
        def scrape(self, url):
            return {}

    def extract(page, url):
        return job(
            company="Acme",
            source_url=url,
            application_url=url,
            description="Build software with Python. " * 10,
            evidence={"structured_job": True, "page_status": 200, "final_url": url},
        )

    monkeypatch.setattr("app.services.job_preview.extract", extract)
    assert (
        inspect_import(Fetch(), "https://jobs.lever.co/fake-company/123", str(source))["verdict"]
        == "UNVERIFIED"
    )
    result = inspect_import(Fetch(), "https://jobs.lever.co/real-company/123", str(source))
    assert result["verdict"] == "VERIFIED"
    assert "evidence" not in result["payload"] and "match" not in result["payload"]
    original_extract = extract
    def client_extract(page, url):
        result = original_extract(page, url)
        result.description += " This role is for one of our clients."
        return result
    monkeypatch.setattr("app.services.job_preview.extract", client_extract)
    client_result = inspect_import(Fetch(), "https://jobs.lever.co/real-company/123", str(source))
    assert client_result["verdict"] == "VERIFIED"
    assert client_result["payload"]["recruiter_client_posting"] is True
    assert "not itself a fraud signal" in client_result["payload"]["verification_reasons"][-1]
    monkeypatch.setattr(
        "app.services.job_preview.socket.getaddrinfo", lambda *a, **kw: [(0, 0, 0, "", ("127.0.0.1", 443))]
    )
    with pytest.raises(ValueError, match="public"):
        inspect_import(Fetch(), "https://jobs.lever.co/real-company/123", str(source))


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/job",
        "https://127.0.0.1/job",
        "https://example.com/a.pdf",
        "https://user:pass@example.com/job",
        "https://localhost/job",
    ],
)
def test_unsafe_import_rejected(url):
    with pytest.raises(ValueError):
        validate_import_url(url)


def test_discovery_has_no_manual_search_or_budget_and_requires_confirmation():
    _, settings, store = setup()
    store.tasks = lambda *a: []
    store.previews = lambda *a: [
        {"id": "p", "payload": {"title": "Test job"}, "verdict": "SUSPICIOUS", "warnings": ["Fee request"]}
    ]
    saves = []
    store.accept_preview = lambda *args: saves.append(args)
    app = create_workspace_app(replace(settings, workspace_worker_enabled="on"), store)
    client = app.test_client()
    csrf = authenticate(client, settings, "alice")
    result = client.get("/discovery", base_url=BASE)
    assert result.status_code == 200
    assert b"weekly_limit" not in result.data and b"Find matching internships" not in result.data
    assert b"Fee request" in result.data and b"Save privately" in result.data
    assert (
        client.post(
            "/discovery", base_url=BASE, data={"csrf": csrf, "action": "accept", "preview_id": "p"}
        ).status_code
        == 200
    )
    assert not saves
    assert (
        client.post(
            "/discovery",
            base_url=BASE,
            data={
                "csrf": csrf,
                "action": "accept",
                "preview_id": "p",
                "confirmed": "yes",
                "acknowledge": "yes",
            },
        ).status_code
        == 303
    )
    assert saves == [("alice", "p", True)]
