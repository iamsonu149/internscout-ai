import json

from app.config import Settings
from app.models import utcnow
from app.services.eligibility import evaluate
from app.services.google_sheets import HEADERS, GoogleSheets
from app.services.matcher import match_job
from tests.test_eligibility import job


def item():
    j = job()
    j.verification_status = "VERIFIED_ATS"
    profile = json.loads(open("profile.json", encoding="utf-8").read())
    return dict(
        j.to_dict(),
        id=1,
        last_seen=utcnow(),
        status="NEW",
        notes="",
        match=match_job(j, profile, evaluate(j, profile)).to_dict(),
    )


class FakeSheets(GoogleSheets):
    def __init__(self, rows):
        self.settings = Settings(min_match_score=0)
        self.tab = "'Opportunities'"
        self.rows, self.writes = rows, []
        self.sheet_properties = {"sheetId": 0}

    def initialize(self):
        return self.rows

    def write_ranges(self, data):
        self.writes.extend(data)


def test_tracking_columns_are_never_overwritten():
    row = [""] * len(HEADERS)
    row[13], row[14] = item()["source_url"], "APPLIED"
    sheet = FakeSheets([HEADERS, row])
    assert sheet.sync([item()])["sheet_added"] == 0
    assert sheet.writes[0]["range"] == "'Opportunities'!B2:N2"
    assert len(sheet.writes[0]["values"][0]) == 13


def test_no_unverified_exports_and_deduplicate_same_batch():
    sheet = FakeSheets([HEADERS])
    assert sheet.sync([dict(item(), verification_status="UNVERIFIED")])["sheet_added"] == 0
    assert sheet.sync([item(), item()])["sheet_added"] == 1


def test_compact_schema_and_evaluation_time():
    from app.services.google_sheets import RejectedSheets, format_evaluated, sheet_row

    assert not {"Relevant Projects", "Source", "Application URL", "Notes"}.intersection(HEADERS)
    assert len(sheet_row(item())) == len(HEADERS) == 15
    assert len(RejectedSheets.headers) == 18
    assert format_evaluated("2026-09-25T17:00:52.123456+00:00") == "2026-09-25 17:00"
    assert format_evaluated("2026-09-25T17:00:52+05:30") == "2026-09-25 11:30"
