"""Single-page import inspection. Nothing is published until the user confirms."""

import ipaddress
import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.models import Job
from app.services.deduplicator import fingerprint, normalize_url
from app.services.firecrawl_service import document_url
from app.services.job_extractor import extract
from app.services.verification_report import client_posting
from app.services.verifier import verify

PUBLIC_FIELDS = (
    "title",
    "company",
    "source_url",
    "application_url",
    "location",
    "remote",
    "employment_type",
    "description",
    "requirements",
    "preferred_skills",
    "graduation_requirement",
    "experience_requirement",
    "salary_or_stipend",
    "deadline",
    "posted_date",
    "verification_status",
    "verification_reasons",
)


def validate_import_url(value, resolve=False):
    if len(value) > 2048:
        raise ValueError("The link is too long.")
    url = normalize_url(value)
    parts = urlsplit(url)
    if parts.scheme != "https" or document_url(url) or parts.path == "/":
        raise ValueError("Paste an individual HTTPS job posting. Documents are not supported.")
    # No user cookies/credentials are sent. Firecrawl performs the sole page fetch.
    if resolve:
        try:
            addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise ValueError("Could not resolve this job website.") from None
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError("Use a public job website.")
    return url


def inspect_import(firecrawl, url, sources_path):
    url = validate_import_url(url, resolve=True)
    page = firecrawl.scrape(url)
    job = extract(page, url)
    if job is None:
        soup = BeautifulSoup(page.get("rawHtml", ""), "html.parser")
        heading = soup.find("h1")
        description = page.get("markdown") or ""
        if not heading or len(description.strip()) < 100:
            raise ValueError("Could not extract clear job details. Try the direct posting link.")
        job = Job(title=heading.get_text(" ", strip=True)[:200],
                  company="Unverified company at " + urlsplit(url).hostname,
                  source_url=url, application_url=url, description=description[:20000],
                  evidence={"unstructured_preview": True})
    sources = json.loads(Path(sources_path).read_text(encoding="utf-8"))
    verify(job, sources.get("official_domains", {}))
    # ATS hosting alone verifies a posting, not the identity of an arbitrary company.
    host = urlsplit(url).hostname
    slug = urlsplit(url).path.strip("/").split("/")[0].casefold()
    board_hosts = {
        "ashby": {"jobs.ashbyhq.com"},
        "greenhouse": {"boards.greenhouse.io", "job-boards.greenhouse.io"},
        "lever": {"jobs.lever.co", "jobs.eu.lever.co"},
        "workable": {"apply.workable.com"},
    }
    configured_company = sources.get("official_domains", {}).get(
        host, ""
    ).casefold() == job.company.casefold() or any(
        b.get("company", "").casefold() == job.company.casefold()
        and b.get("slug", "").casefold() == slug
        and host in board_hosts.get(b.get("type"), set())
        for b in sources.get("boards", [])
    )
    verdict = (
        "VERIFIED"
        if job.verification_status in {"VERIFIED_OFFICIAL", "VERIFIED_ATS"}
        and configured_company
        else "SUSPICIOUS"
        if job.verification_status == "REJECTED"
        else "UNVERIFIED"
    )
    payload = {k: job.to_dict().get(k) for k in PUBLIC_FIELDS}
    payload["inspection_version"] = 3
    payload["verification_checked_at"] = datetime.now(timezone.utc).isoformat()
    payload["company_source_matched"] = configured_company
    payload["recruiter_client_posting"] = client_posting(job.description)
    if client_posting(job.description):
        payload["verification_reasons"] = list(payload["verification_reasons"] or []) + [
            "Recruiter posting for a client: source checks concern the advertiser, not independent verification of the client. This is not itself a fraud signal."
        ]
    # Do not expose a dangerous application destination even on a private preview.
    for key in ("source_url", "application_url"):
        try:
            payload[key] = validate_import_url(payload.get(key) or "")
        except ValueError:
            payload[key] = None
    return {
        "fingerprint": fingerprint(job),
        "payload": payload,
        "verdict": verdict,
        "warnings": ([
            "This recruiter is advertising for a client whose identity has not been independently established."
            if client_posting(job.description) else
            "The company-to-posting link needs more evidence; this does not mean the company is fraudulent."
        ] + job.verification_reasons)
        if verdict != "VERIFIED"
        else [],
    }
