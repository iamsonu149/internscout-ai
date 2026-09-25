from datetime import datetime, timedelta, timezone

from app.models import Job
from app.services.compensation import paid_evidence
from app.services.eligibility import evaluate


def weekly_progress(jobs, profile, now=None):
    """Count unique, still-eligible strong matches first found in the last seven days."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    count = 0
    confirmed_pay = 0
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
                confirmed_pay += bool(paid_evidence(job.salary_or_stipend, job.description))
        except (ValueError, TypeError, KeyError):
            continue
    return {
        "weekly_strong_matches": count,
        "weekly_target": 20,
        "weekly_shortfall": max(0, 20 - count),
        "weekly_confirmed_pay": confirmed_pay,
        "weekly_undisclosed_pay": count - confirmed_pay,
    }
