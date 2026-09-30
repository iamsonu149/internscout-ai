import json
from datetime import date

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
