import json
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import credentials, service_account

from app.models import Job
from app.services.compensation import PAY_REASON, PAY_UNCONFIRMED, compensation_allowed
from app.services.deduplicator import fingerprint, normalize_url
from app.services.http import ProviderError
from app.services.verifier import EXPORTABLE

HEADERS = [
    "Date Found",
    "Company",
    "Job Title",
    "Location",
    "Remote",
    "Internship Type",
    "Match Score",
    "Eligibility",
    "Matching Skills",
    "Missing Skills",
    "Stipend/Salary",
    "Deadline",
    "Verification Status",
    "Job URL",
    "Status",
]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def authorized_session(settings):
    if settings.google_credentials_json:
        cred = service_account.Credentials.from_service_account_info(
            json.loads(settings.google_credentials_json), scopes=SCOPES
        )
    elif settings.google_credentials_file:
        cred = service_account.Credentials.from_service_account_file(
            settings.google_credentials_file, scopes=SCOPES
        )
    elif settings.google_oauth_file:
        cred = credentials.Credentials.from_authorized_user_file(settings.google_oauth_file, scopes=SCOPES)
    else:
        raise ProviderError("Configure service-account credentials or GOOGLE_OAUTH_FILE")
    return AuthorizedSession(cred)


def format_evaluated(value):
    return (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%d %H:%M")
    )


def sheet_row(item):
    match = item["match"]
    return [
        item["discovered_at"][:10],
        item["company"],
        item["title"],
        item.get("location") or "Unknown",
        "Unknown" if item.get("remote") is None else "Yes" if item["remote"] else "No",
        item.get("employment_type") or "Internship (title)",
        match["match_score"],
        match["eligibility"],
        ", ".join(match["matching_skills"]),
        ", ".join(match["missing_skills"]),
        item.get("salary_or_stipend") or PAY_UNCONFIRMED,
        item.get("deadline") or "Unknown",
        item["verification_status"],
        item["source_url"],
        item.get("status", "NEW"),
    ]


class GoogleSheets:
    headers = HEADERS
    end_column = "O"

    def __init__(self, settings, session=None, sleep=time.sleep):
        self.settings = settings
        self.session = session or authorized_session(settings)
        self.sleep = sleep
        self.base = "https://sheets.googleapis.com/v4/spreadsheets/" + quote(settings.sheet_id, safe="")
        self.tab = "'" + settings.sheet_tab.replace("'", "''") + "'"

    def call(self, method, path, retry=True, **kwargs):
        for attempt in range(3 if retry else 1):
            try:
                response = self.session.request(method, self.base + path, timeout=45, **kwargs)
            except requests.RequestException:
                if not retry or attempt == 2:
                    raise ProviderError("Google Sheets network request failed") from None
                self.sleep(2**attempt)
                continue
            if retry and (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                self.sleep(2**attempt)
                continue
            if not 200 <= response.status_code < 300:
                raise ProviderError(f"Google Sheets HTTP {response.status_code}")
            try:
                return response.json()
            except ValueError:
                raise ProviderError("Malformed Google Sheets response") from None
        raise ProviderError("Google retry budget exhausted")

    def initialize(self):
        metadata = self.call("GET", "", params={"fields": "sheets.properties"})
        existing = next(
            (
                s["properties"]
                for s in metadata.get("sheets", [])
                if s["properties"]["title"] == self.settings.sheet_tab
            ),
            None,
        )
        if existing is None:
            result = self.call(
                "POST",
                ":batchUpdate",
                retry=False,
                json={"requests": [{"addSheet": {"properties": {"title": self.settings.sheet_tab}}}]},
            )
            existing = result["replies"][0]["addSheet"]["properties"]
        self.sheet_properties = existing
        rows = self.read_rows()
        if rows and rows[0] != self.headers:
            raise ProviderError(
                "Sheet headers differ; refusing to modify this tab. Use a dedicated empty tab."
            )
        if not rows:
            self.write_ranges([{"range": f"{self.tab}!A1:{self.end_column}1", "values": [self.headers]}])
            sheet_id = existing["sheetId"]
            self.call(
                "POST",
                ":batchUpdate",
                json={
                    "requests": [
                        {
                            "updateSheetProperties": {
                                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                                "fields": "gridProperties.frozenRowCount",
                            }
                        },
                        {
                            "repeatCell": {
                                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                                "cell": {
                                    "userEnteredFormat": {
                                        "backgroundColor": {"red": 0.09, "green": 0.18, "blue": 0.18},
                                        "textFormat": {
                                            "bold": True,
                                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                                        },
                                    }
                                },
                                "fields": "userEnteredFormat",
                            }
                        },
                        {
                            "setBasicFilter": {
                                "filter": {
                                    "range": {
                                        "sheetId": sheet_id,
                                        "startRowIndex": 0,
                                        "startColumnIndex": 0,
                                        "endColumnIndex": len(self.headers),
                                    }
                                }
                            }
                        },
                        {
                            "setDataValidation": {
                                "range": {
                                    "sheetId": sheet_id,
                                    "startRowIndex": 1,
                                    "startColumnIndex": 14,
                                    "endColumnIndex": 15,
                                },
                                "rule": {
                                    "condition": {
                                        "type": "ONE_OF_LIST",
                                        "values": [
                                            {"userEnteredValue": s}
                                            for s in (
                                                "NEW",
                                                "SAVED",
                                                "APPLIED",
                                                "INTERVIEW",
                                                "REJECTED",
                                                "CLOSED",
                                            )
                                        ],
                                    },
                                    "showCustomUi": True,
                                    "strict": True,
                                },
                            }
                        },
                    ]
                },
            )
        return rows or [self.headers]

    def read_rows(self):
        return self.call(
            "GET",
            "/values/" + quote(f"{self.tab}!A:V", safe=""),
            params={"valueRenderOption": "UNFORMATTED_VALUE"},
        ).get("values", [])

    def write_ranges(self, data):
        # RAW prevents formulas from untrusted job content. Fixed ranges make retries idempotent.
        return self.call("POST", "/values:batchUpdate", json={"valueInputOption": "RAW", "data": data})

    def sync(self, items, repo=None):
        rows = self.initialize()
        urls, fingerprints = {}, {}
        for number, raw in enumerate(rows[1:], 2):
            row = raw + [""] * (len(self.headers) - len(raw))
            try:
                urls[normalize_url(row[13])] = (number, row)
            except (ValueError, TypeError):
                pass
            if row[1] and row[2]:
                fingerprints[
                    fingerprint(Job(title=row[2], company=row[1], location=row[3], source_url=""))
                ] = (number, row)
        writes, added, updated = [], 0, 0
        next_row = max(2, len(rows) + 1)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.settings.recheck_days)).isoformat()
        for item in items:
            row = sheet_row(item)
            paid = compensation_allowed(item.get("salary_or_stipend"), item.get("description", ""))
            if not paid:
                row[7] = PAY_REASON
            fresh = item["last_seen"] >= cutoff
            expired = False
            if item.get("deadline"):
                try:
                    expired = date.fromisoformat(item["deadline"][:10]) < date.today()
                except ValueError:
                    expired = True
            if expired:
                row[12] = "REJECTED"
                row[7] = "Deadline passed or invalid — do not apply without rechecking"
            elif not fresh:
                row[12] = "UNVERIFIED"
                row[7] = "Stale evidence — recheck original posting"
            fp = fingerprint(
                Job(
                    title=item["title"], company=item["company"], location=item.get("location"), source_url=""
                )
            )
            key = normalize_url(item["source_url"])
            old = urls.get(key) or fingerprints.get(fp)
            if old:
                number, previous = old
                # Sheet owns tracking after export; preserve it and mirror valid values locally.
                if repo and previous[14] in {"NEW", "SAVED", "APPLIED", "INTERVIEW", "REJECTED", "CLOSED"}:
                    repo.update_tracking(item["id"], previous[14], item.get("notes", ""))
                writes.append({"range": f"{self.tab}!B{number}:N{number}", "values": [row[1:14]]})
                updated += 1
            elif (
                item["verification_status"] in EXPORTABLE
                and item["match"]["match_score"] >= self.settings.min_match_score
                and fresh
                and not expired
                and paid
            ):
                writes.append({"range": f"{self.tab}!A{next_row}:O{next_row}", "values": [row]})
                urls[key] = (next_row, row)
                fingerprints[fp] = (next_row, row)
                next_row += 1
                added += 1
        if writes:
            count = self.sheet_properties.get("gridProperties", {}).get("rowCount", 1000)
            if next_row > count:
                self.call(
                    "POST",
                    ":batchUpdate",
                    json={
                        "requests": [
                            {
                                "updateSheetProperties": {
                                    "properties": {
                                        "sheetId": self.sheet_properties["sheetId"],
                                        "gridProperties": {"rowCount": next_row + 100},
                                    },
                                    "fields": "gridProperties.rowCount",
                                }
                            }
                        ]
                    },
                )
            for offset in range(0, len(writes), 200):
                self.write_ranges(writes[offset : offset + 200])
        result = {"sheet_added": added, "sheet_updated": updated}
        if repo is not None:
            rejected = RejectedSheets(self.settings, session=self.session, sleep=self.sleep)
            result.update(rejected.sync(repo.rejections()))
        return result


class RejectedSheets(GoogleSheets):
    """Audit tab only: excluded listings are never endorsed as accepted opportunities."""

    headers = HEADERS + ["Rejection Reason", "Screening Decision", "Last Evaluated (UTC)"]
    end_column = "R"

    def __init__(self, settings, session=None, sleep=time.sleep):
        if settings.rejected_sheet_tab == settings.sheet_tab:
            raise ValueError("Rejected matches require their own tab")
        super().__init__(replace(settings, sheet_tab=settings.rejected_sheet_tab), session, sleep)

    def sync(self, items, repo=None):
        rows = self.initialize()
        urls, fingerprints = {}, {}
        for number, raw in enumerate(rows[1:], 2):
            row = raw + [""] * (len(self.headers) - len(raw))
            for url in (row[13],):
                try:
                    urls[normalize_url(url)] = number
                except ValueError:
                    pass
            if row[1] and row[2]:
                fingerprints[
                    fingerprint(Job(title=row[2], company=row[1], location=row[3], source_url=""))
                ] = number
        writes, added, updated = [], 0, 0
        next_row = max(2, len(rows) + 1)
        for item in items:
            row = sheet_row(item)
            row += [
                "; ".join(item["rejection_reasons"]),
                item["decision"],
                format_evaluated(item["last_seen"]),
            ]
            fp = fingerprint(
                Job(
                    title=item["title"], company=item["company"], location=item.get("location"), source_url=""
                )
            )
            keys = []
            for url in (item.get("application_url"), item["source_url"]):
                try:
                    keys.append(normalize_url(url))
                except ValueError:
                    pass
            number = next((urls[k] for k in keys if k in urls), None) or fingerprints.get(fp)
            if number:
                # Preserve Date Found and user-managed Status, just like the main tab.
                writes.extend(
                    [
                        {"range": f"{self.tab}!B{number}:N{number}", "values": [row[1:14]]},
                        {"range": f"{self.tab}!P{number}:R{number}", "values": [row[15:18]]},
                    ]
                )
                updated += 1
            else:
                number = next_row
                writes.append({"range": f"{self.tab}!A{number}:R{number}", "values": [row]})
                next_row += 1
                added += 1
            for key in keys:
                urls[key] = number
            fingerprints[fp] = number
        if writes:
            if next_row > self.sheet_properties.get("gridProperties", {}).get("rowCount", 1000):
                self.call(
                    "POST",
                    ":batchUpdate",
                    json={
                        "requests": [
                            {
                                "updateSheetProperties": {
                                    "properties": {
                                        "sheetId": self.sheet_properties["sheetId"],
                                        "gridProperties": {"rowCount": next_row + 100},
                                    },
                                    "fields": "gridProperties.rowCount",
                                }
                            }
                        ]
                    },
                )
            for offset in range(0, len(writes), 200):
                self.write_ranges(writes[offset : offset + 200])
        return {"rejected_sheet_added": added, "rejected_sheet_updated": updated}
