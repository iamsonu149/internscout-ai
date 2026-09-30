"""Explain evidence without treating missing evidence as evidence of fraud."""

import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from app.services.deduplicator import normalize_url


def evidence_link(value):
    try:
        url = normalize_url(value)
        return url if urlsplit(url).scheme == "https" else None
    except ValueError:
        return None


def client_posting(description):
    return bool(re.search(
        r"\b(?:for one of (?:our|their) clients|on behalf of (?:our|a|the) client|"
        r"our client is (?:hiring|looking|seeking))\b", description or "", re.I
    ))


def explain_preview(item, sources_path=None):
    """Read-only presentation, including older previews; never upgrades a verdict."""
    job = item.get("payload") or {}
    client = client_posting(job.get("description"))
    verified = item.get("verdict") == "VERIFIED"
    warnings = item.get("warnings") or []
    status = job.get("verification_status")
    checks = []
    if status in {"VERIFIED_ATS", "VERIFIED_OFFICIAL"}:
        checks.append("Posting: structured job details were extracted from a recognized hiring source. This alone does not establish company identity.")
    else:
        checks.append("Posting: source ownership or job-specific evidence still needs review.")
    if client:
        checks.append("Recruiter posting: the ad says it is for a client. Source checks concern the recruiter; the client's identity has not been independently checked. This alone is not a warning sign.")
    checks.append(
        "Company-to-posting link: matched our reviewed company source records."
        if verified or job.get("company_source_matched") is True else
        "Company-to-posting link: not independently established by the available evidence. A missing match is not a finding of fraud."
    )
    # Keep actual failure reasons, but replace the old blanket identity message.
    details = [w for w in warnings if w and not w.startswith("Company identity could not")
               and not w.startswith("Fetched single JobPosting")]
    if job.get("inspection_version") == 3:
        details = list(job.get("verification_reasons") or [])
    references = []
    if sources_path:
        try:
            records = json.loads(Path(sources_path).read_text(encoding="utf-8"))
            for entry in records.get("organization_references", []):
                checked = date.fromisoformat(entry["checked_on"])
                if (entry["company"].casefold() == (job.get("company") or "").casefold()
                        and 0 <= (date.today() - checked).days <= 90):
                    references.append(entry)
        except (OSError, ValueError, KeyError, TypeError):
            references = []
    return {
        "heading": "Verification incomplete" if not verified else "Source checks passed",
        "checks": checks,
        "details": details,
        "client_posting": client,
        "references": references,
        "checked_at": job.get("verification_checked_at"),
        "source_url": evidence_link(job.get("source_url")),
        "application_url": evidence_link(job.get("application_url")),
    }
