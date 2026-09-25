import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.database.repository import Repository
from app.services.closure_check import check_closure
from app.services.credit_budget import BudgetExceeded, CreditBudget
from app.services.eligibility import evaluate
from app.services.firecrawl_service import Firecrawl
from app.services.http import Http, ProviderError
from app.services.matcher import match_job
from tests.test_eligibility import job


def test_budget_persists_reservations_on_failure_and_rolls(tmp_path):
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    repo = Repository(str(tmp_path / "db"))
    budget = CreditBudget(repo, 3, clock=lambda: now)
    rid = budget.reserve("search", 2)
    assert CreditBudget(repo, 3, clock=lambda: now).summary()["credits_budgeted_7d"] == 2
    with pytest.raises(BudgetExceeded):
        budget.reserve("search", 2)
    budget.finish(rid, 1, "request-id")
    assert budget.summary()["credits_budgeted_7d"] == 2
    assert budget.summary()["credits_provider_reported_7d"] == 1
    later = CreditBudget(repo, 3, clock=lambda: now + timedelta(days=8))
    assert later.summary()["credits_budgeted_7d"] == 0


def test_reported_overage_prevents_next_request(tmp_path):
    budget = CreditBudget(Repository(str(tmp_path / "db")), 4)
    budget.finish(budget.reserve("scrape", 1), 5)
    with pytest.raises(BudgetExceeded):
        budget.reserve("scrape", 1)


def test_paid_request_reserves_before_timeout_and_never_retries(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"success": True, "data": {"remainingCredits": 20}})
        raise httpx.ReadTimeout("timeout")

    budget = CreditBudget(Repository(str(tmp_path / "db")), 2)
    service = Firecrawl("fake", Http(httpx.Client(transport=httpx.MockTransport(handler))), budget)
    with pytest.raises(ProviderError):
        service.search("internship", limit=10)
    with pytest.raises(BudgetExceeded):
        service.search("internship", limit=10)
    assert calls == ["GET", "POST"]
    assert budget.summary()["credits_budgeted_7d"] == 2


def test_credit_api_failure_blocks_paid_call(tmp_path):
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(503)

    budget = CreditBudget(Repository(str(tmp_path / "db")), 400)
    service = Firecrawl("fake", Http(httpx.Client(transport=httpx.MockTransport(handler))), budget)
    with pytest.raises(ProviderError):
        service.search("intern")
    assert methods == ["GET"]
    assert budget.summary()["credits_budgeted_7d"] == 0


@pytest.mark.parametrize(
    "status,body,result",
    [
        (404, "", "CLOSED"),
        (410, "", "CLOSED"),
        (403, "", "UNKNOWN"),
        (429, "", "UNKNOWN"),
        (200, "This job is closed.", "CLOSED"),
        (200, "No longer accepting applications", "CLOSED"),
        (200, "Welcome to careers", "UNKNOWN"),
        (200, "<script>this job is closed</script>Apply here", "UNKNOWN"),
        (200, "<div hidden>This job is closed</div>Apply now", "UNKNOWN"),
        (200, "This job is closed. Apply now", "UNKNOWN"),
    ],
)
def test_closure_requires_explicit_evidence(status, body, result):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(status, text=body, headers={"content-type": "text/html"})
        )
    )
    assert check_closure("https://jobs.lever.co/acme/1", {"jobs.lever.co"}, client) == result


def test_closure_does_not_follow_unsafe_redirect():
    calls = []

    def handler(r):
        calls.append(str(r.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    assert (
        check_closure(
            "https://jobs.lever.co/acme/1",
            {"jobs.lever.co"},
            httpx.Client(transport=httpx.MockTransport(handler)),
        )
        == "UNKNOWN"
    )
    assert len(calls) == 1


def test_required_skills_weighted_more_than_preferred():
    profile = json.load(open("profile.json", encoding="utf-8"))
    a = job(description="Python required. Java preferred.")
    b = replace(a, description="Java required. Python preferred.")
    assert (
        match_job(a, profile, evaluate(a, profile)).breakdown["skills"]
        > match_job(b, profile, evaluate(b, profile)).breakdown["skills"]
    )
    assert any(
        "required-skill gaps" in c for c in match_job(b, profile, evaluate(b, profile)).eligibility_concerns
    )


def test_availability_and_degree_restrictions():
    profile = {
        "graduation_year": 2027,
        "education": "BS Data Science",
        "availability": {
            "full_time": True,
            "max_hours_per_day": 10,
            "max_months": None,
            "available_from": "2026-09-25",
        },
    }
    assert evaluate(job(description="Full-time, 6 months, 8 hours per day."), profile).accepted
    assert not evaluate(job(description="Required 12 hours per day."), profile).accepted
    assert not evaluate(job(description="BTech only."), profile).accepted
    assert not evaluate(job(description="Start date: 2026-09-01."), profile).accepted
    assert evaluate(job(description="Bachelor in Computer Science or related field."), profile).accepted
