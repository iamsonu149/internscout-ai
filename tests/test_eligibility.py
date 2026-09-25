from app.models import Job
from app.services.eligibility import evaluate
from app.services.verifier import verify


def job(**overrides):
    values = dict(
        title="Backend Engineer Intern",
        company="Acme",
        salary_or_stipend="INR 25,000 per month",
        source_url="https://jobs.lever.co/acme/123",
        application_url="https://jobs.lever.co/acme/123/apply",
        location="India",
        description="Build Python APIs. " * 10,
    )
    return Job(**(values | overrides))


def test_reject_incompatible_graduation_and_remote():
    assert not evaluate(job(graduation_requirement="Graduating in 2026"), {"graduation_year": 2027}).accepted
    assert not evaluate(job(location="Remote", remote=True), {"graduation_year": 2027}).accepted
    assert evaluate(job(location="Remote worldwide", remote=True), {"graduation_year": 2027}).accepted


def test_expiry_experience_and_degree():
    for overrides in (
        {"deadline": "2020-01-01"},
        {"experience_requirement": "3+ years of experience"},
        {"description": "Requires a PhD in machine learning"},
        {"description": "Applications are closed"},
    ):
        assert not evaluate(job(**overrides), {"graduation_year": 2027}).accepted


def test_explicit_applicant_country_overrides_office_location():
    item = job(
        location="India; eligible: United States",
        remote=True,
        evidence={"applicant_locations": "United States"},
    )
    assert not evaluate(item, {"graduation_year": 2027}).accepted


def test_trust_requires_evidence_and_exact_domain():
    assert verify(job(), {}).verification_status == "UNVERIFIED"
    assert (
        verify(job(evidence={"structured_job": True, "page_status": 200}), {}).verification_status
        == "VERIFIED_ATS"
    )
    assert (
        verify(
            job(source_url="https://jobs.lever.co.evil.com/a", evidence={"structured_job": True}), {}
        ).verification_status
        == "UNVERIFIED"
    )
    assert verify(job(application_url="javascript:alert(1)"), {}).verification_status == "REJECTED"
