import json
from datetime import date

import pytest

from app.services.verification_report import explain_preview
from app.services.verifier import verify
from tests.test_eligibility import job


def test_different_ats_tenant_or_redirect_does_not_verify():
    for overrides in (
        {"application_url": "https://jobs.lever.co/impostor/123/apply"},
        {"evidence": {"structured_job": True, "page_status": 200,
                      "final_url": "https://jobs.lever.co/impostor/123"}},
    ):
        item = job(evidence={"structured_job": True, "page_status": 200})
        for key, value in overrides.items():
            setattr(item, key, value)
        assert verify(item, {}).verification_status == "UNVERIFIED"


def test_missing_content_is_not_fraud_evidence():
    assert verify(job(description="Internship"), {}).verification_status == "UNVERIFIED"


@pytest.mark.parametrize("text", [
    "We never ask applicants to pay a registration fee.",
    "No application fee is required.",
    "An application fee is not required.",
    "Avoid guaranteed placement offers.",
    "We do not offer guaranteed placement.",
])
def test_safety_statements_are_not_risk_flags(text):
    result = verify(job(description="Build Python APIs. " * 10 + text,
                        evidence={"structured_job": True, "page_status": 200}), {})
    assert result.verification_status == "VERIFIED_ATS"


@pytest.mark.parametrize("text", [
    "You must pay a registration fee to proceed.",
    "A training fee is mandatory.",
    "We offer guaranteed placement.",
    "No experience required, you must pay an application fee.",
    "We never charge application fees, but a training fee is required.",
])
def test_warning_quotes_identify_positive_demand(text):
    result = verify(job(description="Build Python APIs. " * 10 + text,
                        evidence={"structured_job": True, "page_status": 200}), {})
    assert result.verification_status == "REJECTED"
    assert "warning:" in result.verification_reasons[0]


def test_missing_application_link_is_unknown_not_suspicious():
    assert verify(job(application_url=None), {}).verification_status == "UNVERIFIED"


def test_client_and_independent_reference_never_upgrade_preview(tmp_path):
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps({"organization_references": [{
        "company": "Acme", "checked_on": date.today().isoformat(),
        "url": "https://example.org/company/acme", "scope": "Identity only",
    }]}))
    item = {"verdict": "UNVERIFIED", "payload": {
        "company": "Acme", "description": "This role is for one of our clients",
        "verification_status": "VERIFIED_ATS",
    }, "warnings": []}
    report = explain_preview(item, sources)
    assert report["client_posting"]
    assert len(report["references"]) == 1
    assert report["heading"] == "Verification incomplete"
    assert item["verdict"] == "UNVERIFIED"
    item["payload"]["company"] = "Acme impostor"
    assert not explain_preview(item, sources)["references"]
