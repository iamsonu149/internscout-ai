from datetime import datetime, timedelta, timezone

from app.models import Job
from app.services.eligibility import evaluate


def weekly_progress(jobs, profile, now=None):
    """Count unique, still-eligible strong matches first found in the last seven days."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    count = 0
    for row in jobs:
        try:
            discovered = datetime.fromisoformat(row["discovered_at"])
            fresh = datetime.fromisoformat(row["last_seen"])
            job = Job(**{key: value for key, value in row.items() if key in Job.__dataclass_fields__})
            if (
                cutoff <= discovered <= now
                and cutoff <= fresh <= now
                and row["match"]["match_score"] >= 80
                and row["verification_status"] in {"VERIFIED_ATS", "VERIFIED_OFFICIAL"}
                and row.get("status") != "CLOSED"
                and evaluate(job, profile, today=now.date()).accepted
            ):
                count += 1
        except (ValueError, TypeError, KeyError):
            continue
    return {"weekly_strong_matches": count, "weekly_target": 14, "weekly_shortfall": max(0, 14 - count)}
