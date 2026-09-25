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
    if rows[0] != headers:
        raise ProviderError("Unexpected dashboard sheet headers")
    jobs = []
    for number, values in enumerate(rows[1:], 2):
        row = [str(value) if value is not None else "" for value in values]
        row += [""] * max(0, len(headers) - len(row))
        if not row[1].strip() or not row[2].strip():
            continue
        try:
            score = float(row[6])
            score = round(score) if 0 <= score <= 100 else 0
        except (ValueError, OverflowError):
            score = 0

        def split(value):
            return [part.strip() for part in value.split(",") if part.strip()]

        jobs.append(
            {
                "id": number,
                "company": row[1],
                "title": row[2],
                "location": row[3],
                "remote": True if row[4].lower() == "yes" else False if row[4].lower() == "no" else None,
                "employment_type": row[5],
                "salary_or_stipend": row[11],
                "deadline": row[12],
                "source_type": row[13],
                "verification_status": row[14],
                "application_url": safe_url(row[15]),
                "source_url": safe_url(row[16]),
                "status": row[17] or "NEW",
                "notes": row[18],
                "discovered_at": row[0],
                "last_seen": row[21] if rejected else None,
                "verification_reasons": ["Verification status reported by Google Sheets"],
                "rejection_reasons": [row[19]] if rejected else [],
                "decision": row[20] if rejected else "MATCHED",
                "sheet_backed": True,
                "match": {
                    "match_score": score,
                    "eligibility": row[7],
                    "matching_skills": split(row[8]),
                    "missing_skills": split(row[9]),
                    "relevant_projects": split(row[10]),
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
