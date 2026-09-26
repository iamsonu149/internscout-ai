import pytest
from bs4 import BeautifulSoup

from app.dashboard import create_app
from app.services.link_import import validate_link
from app.services.pipeline import Pipeline
from tests.test_eligibility import job
from tests.test_hosted_dashboard import BASE, Feed, config, login
from tests.test_pipeline import config as pipeline_config


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a/b",
        "https://evil.example/a/b",
        "https://jobs.lever.co/acme/file.pdf",
        "https://jobs.lever.co/acme",
    ],
)
def test_unsafe_or_document_imports_rejected(url):
    with pytest.raises(ValueError):
        validate_link(url)


def test_import_requires_login_csrf_and_single_use_submission(tmp_path, monkeypatch):
    settings = config(tmp_path)
    client = create_app(settings, sheet_feed=Feed()).test_client()
    calls = []
    monkeypatch.setattr("app.services.link_import.dispatch_import", lambda *args: calls.append(args))
    assert client.post("/import", base_url=BASE).status_code == 303
    login(client, settings)
    page = client.get("/import", base_url=BASE)
    soup = BeautifulSoup(page.data, "html.parser")
    form = {i["name"]: i.get("value", "") for i in soup.select('input[type="hidden"]')}
    form["job_url"] = "https://jobs.lever.co/acme/123"
    assert client.post("/import", base_url=BASE, data={**form, "csrf": "bad"}).status_code == 400
    assert not calls
    assert client.post("/import", base_url=BASE, data=form).status_code == 303
    assert len(calls) == 1
    assert client.post("/import", base_url=BASE, data=form).status_code == 400
    assert len(calls) == 1


def test_import_scrapes_once_reuses_saved_job_and_syncs(tmp_path, monkeypatch):
    settings = pipeline_config(tmp_path)
    settings.sheet_id = "test"
    settings.min_match_score = 0

    class Firecrawl:
        calls = 0

        def scrape(self, url):
            self.calls += 1
            return {}

    class Sheets:
        calls = 0

        def sync(self, jobs, repo):
            self.calls += 1
            return {"sheet_added": 1}

    fc, sheets = Firecrawl(), Sheets()
    monkeypatch.setattr(
        "app.services.pipeline.extract", lambda page, url: job(evidence={"public_api": True, "active": True})
    )
    pipeline = Pipeline(settings, firecrawl=fc, sheets=sheets)
    result = pipeline.import_url("https://jobs.lever.co/acme/123")
    assert result["matched"] == 1 and sheets.calls == 1
    pipeline.import_url("https://jobs.lever.co/acme/123/apply")
    assert fc.calls == 1 and sheets.calls == 2 and len(pipeline.repo.jobs()) == 1


def test_unextractable_import_never_writes_sheet(tmp_path, monkeypatch):
    settings = pipeline_config(tmp_path)
    settings.sheet_id = "test"

    class Firecrawl:
        def scrape(self, url):
            return {"rawHtml": "<p>No posting</p>"}

    pipeline = Pipeline(settings, firecrawl=Firecrawl())
    assert "No row added" in pipeline.import_url("https://jobs.lever.co/acme/123")["outcome"]
    assert not pipeline.repo.jobs()
