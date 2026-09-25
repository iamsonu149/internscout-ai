from dataclasses import replace

from app.services.google_sheets import RejectedSheets
from app.services.pipeline import Pipeline
from tests.test_pipeline import FakeATS, config
from tests.test_sheets import FakeSheets, item


def metrics():
    return dict(filtered=0, verified=0, llm_calls=0, matched=0, added=0, duplicates=0)


def test_partial_match_rejected_by_cohort_is_saved_once_and_promoted(tmp_path):
    pipeline = Pipeline(config(tmp_path))
    job = FakeATS().fetch({})[0]
    bad = replace(job, graduation_requirement="Graduating in 2026")
    pipeline.process(bad, metrics())
    pipeline.process(bad, metrics())
    rejected = pipeline.repo.rejections()
    assert len(rejected) == 1 and not pipeline.repo.jobs()
    assert any("2027" in reason for reason in rejected[0]["rejection_reasons"])
    pipeline.process(job, metrics())
    assert len(pipeline.repo.jobs()) == 1
    assert pipeline.repo.rejections()[0]["decision"] == "NOW MATCHED"
    pipeline.process(bad, metrics())
    assert pipeline.repo.rejections()[0]["decision"] == "EXCLUDED"


def test_irrelevant_jobs_excluded_but_low_match_retained(tmp_path):
    settings = config(tmp_path)
    settings.min_match_score = 100
    pipeline = Pipeline(settings)
    job = FakeATS().fetch({})[0]
    pipeline.process(replace(job, title="Sales Intern"), metrics())
    assert not pipeline.repo.rejections()
    pipeline.process(job, metrics())
    assert "below the required 100" in pipeline.repo.rejections()[0]["rejection_reasons"][0]
    assert not pipeline.repo.jobs()

    settings.min_match_score = 60
    pipeline.process(job, metrics())
    assert len(pipeline.repo.jobs()) == 1
    settings.min_match_score = 100
    pipeline.process(job, metrics())
    assert pipeline.repo.jobs()[0]["verification_status"] == "REJECTED"


def test_unverified_partial_match_with_missing_application_is_audit_only(tmp_path):
    pipeline = Pipeline(config(tmp_path))
    job = replace(FakeATS().fetch({})[0], application_url=None)
    pipeline.process(job, metrics())
    assert not pipeline.repo.jobs()
    assert pipeline.repo.rejections()[0]["application_url"] is None
    assert pipeline.repo.rejections()[0]["verification_status"] == "REJECTED"


class FakeRejected(FakeSheets, RejectedSheets):
    headers = RejectedSheets.headers
    sync = RejectedSheets.sync


def rejected_item():
    return item() | {"rejection_reasons": ["Graduation cohort incompatible"], "decision": "EXCLUDED"}


def test_rejection_sync_deduplicates_and_preserves_notes():
    sheet = FakeRejected([RejectedSheets.headers])
    assert sheet.sync([rejected_item(), rejected_item()])["rejected_sheet_added"] == 1
    assert sheet.writes[0]["values"][0][19] == "Graduation cohort incompatible"
    row = sheet.writes[0]["values"][0]
    row[17:19] = ["SAVED", "Check with recruiter"]
    sheet = FakeRejected([RejectedSheets.headers, row])
    result = sheet.sync([rejected_item() | {"decision": "NOW MATCHED"}])
    assert result["rejected_sheet_added"] == 0
    assert all("R2" not in write["range"] and "S2" not in write["range"] for write in sheet.writes)
    assert sheet.writes[1]["values"][0][1] == "NOW MATCHED"
