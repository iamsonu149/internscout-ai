"""Read-only Sheets view, isolated from discovery persistence and export."""

import copy
import threading
import time
from datetime import datetime, timezone

from app.services.deduplicator import normalize_url
from app.services.google_sheets import GoogleSheets, RejectedSheets
from app.services.http import ProviderError


def safe_url(value):
    try:
        return normalize_url(value)
    except (ValueError, TypeError):
        return None


def parse_rows(rows, headers, rejected=False):
    if not rows:
        return []
    actual_headers = rows[0]
    required = {
        "Date Found",
        "Company",
        "Job Title",
        "Job URL",
        "Status",
        "Match Score",
        "Verification Status",
    }
    if not required.issubset(actual_headers) or len(actual_headers) != len(set(actual_headers)):
        raise ProviderError("Unexpected dashboard sheet headers")
    jobs = []
    for number, values in enumerate(rows[1:], 2):
        row = [str(value) if value is not None else "" for value in values]
        row += [""] * max(0, len(actual_headers) - len(row))
        values_by_header = dict(zip(actual_headers, row))

        def value(name):
            return values_by_header.get(name, "")

        if not value("Company").strip() or not value("Job Title").strip():
            continue
        try:
            score = float(value("Match Score"))
            score = round(score) if 0 <= score <= 100 else 0
        except (ValueError, OverflowError):
            score = 0

        def split(value):
            return [part.strip() for part in value.split(",") if part.strip()]

        jobs.append(
            {
                "id": number,
                "company": value("Company"),
                "title": value("Job Title"),
                "location": value("Location"),
                "remote": True
                if value("Remote").lower() == "yes"
                else False
                if value("Remote").lower() == "no"
                else None,
                "employment_type": value("Internship Type"),
                "salary_or_stipend": value("Stipend/Salary"),
                "deadline": value("Deadline"),
                "source_type": value("Source"),
                "verification_status": value("Verification Status"),
                "application_url": safe_url(value("Application URL"))
                if "Application URL" in actual_headers
                else safe_url(value("Job URL")),
                "source_url": safe_url(value("Job URL")),
                "status": value("Status") or "NEW",
                "notes": value("Notes"),
                "discovered_at": value("Date Found"),
                "last_seen": value("Last Evaluated (UTC)") if rejected else None,
                "verification_reasons": ["Verification status reported by Google Sheets"],
                "rejection_reasons": [value("Rejection Reason")] if rejected else [],
                "decision": value("Screening Decision") if rejected else "MATCHED",
                "sheet_backed": True,
                "match": {
                    "match_score": score,
                    "eligibility": value("Eligibility"),
                    "matching_skills": split(value("Matching Skills")),
                    "missing_skills": split(value("Missing Skills")),
                    "relevant_projects": split(value("Relevant Projects")),
                    "reason": "Match score and skills from your latest sheet data.",
                    "eligibility_concerns": [],
                    "breakdown": {},
                    "ai_notes": None,
                },
            }
        )
    return jobs


class SheetDashboardFeed:
    def __init__(self, settings, factory=GoogleSheets, clock=time.monotonic):
        self.settings, self.factory, self.clock = settings, factory, clock
        self.cache = {"jobs": [], "rejected": [], "synced_at": None, "error": None}
        self.checked_at = None
        self.lock = threading.Lock()

    def read(self, force=False):
        with self.lock:
            if not force and self.checked_at is not None and self.clock() - self.checked_at < 60:
                return copy.deepcopy(self.cache)
            try:
                sheet = self.factory(self.settings)
                rejected = RejectedSheets(self.settings, session=sheet.session)
                # No initialize/sync calls: opening the dashboard never changes the spreadsheet.
                jobs = parse_rows(sheet.read_rows(), GoogleSheets.headers)
                exclusions = parse_rows(rejected.read_rows(), RejectedSheets.headers, rejected=True)
                self.cache = {
                    "jobs": jobs,
                    "rejected": exclusions,
                    "synced_at": datetime.now(timezone.utc).isoformat(),
                    "error": None,
                }
            except Exception:
                self.cache["error"] = (
                    "Could not refresh Google Sheets. Showing the last loaded data, if available. Try again shortly."
                )
            self.checked_at = self.clock()
            return copy.deepcopy(self.cache)
