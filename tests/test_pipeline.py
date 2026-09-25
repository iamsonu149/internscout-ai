import json
from dataclasses import replace

from app.config import Settings
from app.services.pipeline import Pipeline
from tests.test_eligibility import job


class FakeFirecrawl:
    def search(self, query, limit):
        return [
            {"url": "https://jobs.lever.co/acme/broken", "title": "Backend Intern"},
            {"url": "https://jobs.lever.co/acme/123", "title": "Backend Intern"},
        ]

    def scrape(self, url):
        raise RuntimeError("simulated failed page")


class FakeATS:
    def fetch(self, board):
        item = job(
            description="Undergraduate students: build Python Flask PostgreSQL REST APIs. " * 4,
            evidence={"public_api": True, "active": True},
            source_type="lever",
        )
        return [item, replace(item)]


def config(tmp_path):
    sources = tmp_path / "sources.json"
    sources.write_text(
        json.dumps({"boards": [{"type": "lever", "slug": "acme", "company": "Acme"}], "official_domains": {}})
    )
    return Settings(
        database_path=str(tmp_path / "db.sqlite"),
        sources_path=str(sources),
        firecrawl_api_key="fake",
        max_queries=1,
    )


def test_pipeline_failure_isolation_and_rerun_dedup(tmp_path):
    pipeline = Pipeline(config(tmp_path), firecrawl=FakeFirecrawl(), ats=FakeATS())
    result = pipeline.run(sync=False)
    assert result["state"] == "PARTIAL"
    assert result["added"] == 1 and result["errors"] == 1
    assert len(pipeline.repo.jobs()) == 1
    again = pipeline.run(sync=False)
    assert again["added"] == 0
    assert len(pipeline.repo.jobs()) == 1
    assert len(pipeline.repo.runs()) == 2


def test_missing_sources_is_failure_not_empty_success(tmp_path):
    settings = config(tmp_path)
    settings.firecrawl_api_key = ""

    class NoSource:
        def fetch(self, board):
            raise RuntimeError("failure")

    assert Pipeline(settings, ats=NoSource()).run(sync=False)["state"] == "FAILED"


def test_sheet_failure_does_not_lose_jobs(tmp_path):
    settings = config(tmp_path)
    settings.firecrawl_api_key = ""
    settings.sheet_id = "fake"

    class BrokenSheets:
        def sync(self, jobs, repo):
            raise RuntimeError("network")

    pipeline = Pipeline(settings, ats=FakeATS(), sheets=BrokenSheets())
    assert pipeline.run()["state"] == "PARTIAL"
    assert len(pipeline.repo.jobs()) == 1
