import pytest

from app.services.compensation import PAY_REASON, PAY_UNCONFIRMED, compensation_allowed, paid_evidence
from app.services.eligibility import evaluate
from app.services.google_sheets import HEADERS
from app.services.job_extractor import enrich
from tests.test_eligibility import job
from tests.test_sheets import FakeSheets, item


@pytest.mark.parametrize(
    "salary,description",
    [
        ("INR 25,000 per month", ""),
        (None, "This is a paid internship."),
        (None, "Stipend: INR 15,000 per month."),
        ('{"currency":"INR","value":{"minValue":10000,"maxValue":20000}}', ""),
    ],
)
def test_positive_pay_evidence(salary, description):
    assert paid_evidence(salary, description)
    assert enrich(job(salary_or_stipend=salary, description=description)).salary_or_stipend


@pytest.mark.parametrize(
    "salary,description",
    [
        (None, "Python internship. Paid time off and insurance."),
        ("Unknown", "Work on software."),
        (None, "This is an unpaid internship."),
        ("INR 0", ""),
        (None, "This is not a paid internship."),
        (None, "Stipend is performance-based. INR 20,000 maximum."),
        (None, "Our company raised $1000000 in funding."),
        ('{"currency":"INR","value":{"minValue":0,"maxValue":20000}}', ""),
    ],
)
def test_uncertain_pay_is_distinguished_from_unpaid(salary, description):
    j = job(salary_or_stipend=salary, description=description)
    assert not paid_evidence(salary, description)
    result = evaluate(j, {"graduation_year": 2027})
    if compensation_allowed(salary, description):
        assert result.accepted and PAY_UNCONFIRMED in result.concerns
    else:
        assert PAY_REASON in result.reasons


def test_unknown_pay_can_export_but_unpaid_cannot():
    sheet = FakeSheets([HEADERS])
    assert sheet.sync([item() | {"salary_or_stipend": "Unpaid"}])["sheet_added"] == 0
    assert sheet.sync([item() | {"salary_or_stipend": None}])["sheet_added"] == 1


def test_paid_full_time_job_is_not_an_internship():
    result = evaluate(job(title="Software Engineer", employment_type="Full-time"), {"graduation_year": 2027})
    assert "Not explicitly an internship" in result.reasons


def test_other_technical_internships_still_need_profile_and_location():
    assert evaluate(job(title="Frontend Developer Intern"), {"graduation_year": 2027}).accepted
    assert not evaluate(job(title="Marketing Intern"), {"graduation_year": 2027}).accepted
