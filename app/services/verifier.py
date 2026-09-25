import re
from urllib.parse import urlsplit

from app.services.deduplicator import normalize_url

ATS_HOSTS = {
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.eu.lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
}
EXPORTABLE = {"VERIFIED_OFFICIAL", "VERIFIED_ATS", "LIKELY_GENUINE"}


def verify(job, official_domains):
    reasons = []
    try:
        source = normalize_url(job.source_url)
        application = normalize_url(job.application_url)
        final = normalize_url(job.evidence.get("final_url") or source)
    except ValueError:
        job.verification_status = "REJECTED"
        job.verification_reasons = ["Missing or unsafe source/application URL"]
        return job
    host, app_host, final_host = (urlsplit(u).hostname for u in (source, application, final))
    if len(job.description.strip()) < 100 or not job.company.strip() or not job.title.strip():
        reasons.append("Insufficient job-specific content")
    if re.search(
        r"(?:pay|payment|deposit).{0,35}(?:registration|application|training) fee|(?:registration|application) fee.{0,20}(?:required|pay)|guaranteed (?:job|placement)",
        job.description,
        re.I,
    ):
        reasons.append("Suspicious fee or placement claim")
    configured = official_domains.get(host)
    official = bool(configured and configured.casefold() == job.company.casefold())
    if reasons:
        status = "REJECTED"
    elif job.evidence.get("public_api") and job.evidence.get("active"):
        status = (
            "VERIFIED_ATS"
            if (
                app_host in ATS_HOSTS
                or official_domains.get(app_host, "").casefold() == job.company.casefold()
            )
            else "UNVERIFIED"
        )
        reasons.append(
            "Present in configured company's public ATS board"
            if status == "VERIFIED_ATS"
            else "ATS application destination needs review"
        )
    elif (
        job.evidence.get("structured_job")
        and job.evidence.get("page_status") in (200, 201, 202, 203, 204, 206, 304)
        and host == final_host
        and (host in ATS_HOSTS or official)
    ):
        if app_host == host or app_host in ATS_HOSTS:
            status = "VERIFIED_OFFICIAL" if official else "VERIFIED_ATS"
            reasons.append("Fetched single JobPosting with company, description and application destination")
        else:
            status = "UNVERIFIED"
            reasons.append("Application destination is outside trusted source")
    else:
        status = "UNVERIFIED"
        reasons.append("Source ownership or job-specific evidence is insufficient")
    job.verification_status, job.verification_reasons = status, reasons
    return job
