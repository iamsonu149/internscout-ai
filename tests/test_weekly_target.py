from datetime import datetime, timezone

from app.services.weekly_target import weekly_progress
from tests.test_eligibility import job


def test_weekly_target_excludes_stale_weak_expired_and_ineligible():
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    row = job(discovered_at="2026-09-24T00:00:00+00:00", verification_status="VERIFIED_ATS").to_dict()
    row.update(last_seen="2026-09-24T00:00:00+00:00", match={"match_score": 85})
    rows = [
        row,
        row | {"match": {"match_score": 79}},
        row | {"discovered_at": "2026-09-01T00:00:00+00:00"},
        row | {"verification_status": "UNVERIFIED"},
        row | {"deadline": "2026-09-20"},
        row | {"location": "United States"},
        row | {"status": "CLOSED"},
        row | {"last_seen": "2026-09-01T00:00:00+00:00"},
    ]
    result = weekly_progress(rows, {"graduation_year": 2027}, now)
    assert result == {"weekly_strong_matches": 1, "weekly_target": 14, "weekly_shortfall": 13}
