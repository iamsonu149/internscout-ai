import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import requests

from app.config import Settings
from app.services.firecrawl_service import Firecrawl
from app.services.google_sheets import HEADERS, GoogleSheets
from app.services.http import Http, ProviderError
from tests.test_sheets import FakeSheets, item


def test_firecrawl_page_failure_even_when_api_successful():
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, json={"success": True, "data": {"markdown": "Not found", "metadata": {"statusCode": 404}}}
        )
    )
    with pytest.raises(ProviderError, match="unavailable"):
        Firecrawl("fake", Http(httpx.Client(transport=transport))).scrape("https://example.com/job")


def test_stale_and_expired_jobs_not_exported():
    sheet = FakeSheets([HEADERS])
    stale = item() | {"last_seen": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()}
    expired = item() | {"deadline": "2020-01-01"}
    assert sheet.sync([stale, expired])["sheet_added"] == 0


def test_sheet_retry_is_same_fixed_range_and_raw():
    calls = []

    class Session:
        def request(self, method, url, **kwargs):
            calls.append(kwargs["json"])
            if len(calls) == 1:
                raise requests.Timeout()
            response = requests.Response()
            response.status_code = 200
            response._content = b"{}"
            return response

    sheet = GoogleSheets(Settings(sheet_id="test"), session=Session(), sleep=lambda _: None)
    sheet.write_ranges([{"range": "'Opportunities'!A2:S2", "values": [["=malicious()"]]}])
    assert calls[0] == calls[1]
    assert calls[0]["valueInputOption"] == "RAW"


def test_no_secret_in_settings_repr():
    assert "super-secret" not in repr(
        Settings(firecrawl_api_key="super-secret", google_credentials_json="super-secret")
    )


def test_dashboard_escapes_job_content_and_saves_csrf_form(tmp_path):
    from app.dashboard import create_app
    from app.database.repository import Repository
    from app.services.eligibility import evaluate
    from app.services.matcher import match_job
    from tests.test_eligibility import job

    settings = Settings(database_path=str(tmp_path / "test.db"))
    repo = Repository(settings.database_path)
    profile = json.loads(open("profile.json", encoding="utf-8").read())
    j = job(title="Python Intern <script>alert(1)</script>")
    j.verification_status = "VERIFIED_ATS"
    job_id, _ = repo.upsert(j, match_job(j, profile, evaluate(j, profile)))
    client = create_app(settings).test_client()
    response = client.get("/")
    assert b"<script>alert" not in response.data and b"&lt;script&gt;" in response.data
    with client.session_transaction() as session:
        csrf = session["csrf"]
    response = client.post(
        f"/jobs/{job_id}/tracking", data={"csrf": csrf, "status": "SAVED", "notes": "Interested"}
    )
    assert response.status_code == 303
    assert repo.jobs()[0]["status"] == "SAVED"
