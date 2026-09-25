import json
import re
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.models import Job


def plain(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return BeautifulSoup(unescape(str(value)), "html.parser").get_text(" ", strip=True)


def nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from nodes(item)
    elif isinstance(value, dict):
        kind = value.get("@type", [])
        if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
            yield value
        if "@graph" in value:
            yield from nodes(value["@graph"])


def location_text(value):
    if isinstance(value, list):
        return "; ".join(filter(None, (location_text(item) for item in value))) or None
    if not isinstance(value, dict):
        return plain(value)
    address = value.get("address", value)
    if not isinstance(address, dict):
        return plain(address)
    parts = [address.get(key) for key in ("addressLocality", "addressRegion", "addressCountry")]
    return ", ".join(
        plain(p.get("name")) if isinstance(p, dict) else str(p) for p in parts if p
    ) or value.get("name")


def enrich(job):
    sentences = re.split(r"[\n.;]", job.description)
    job.graduation_requirement = job.graduation_requirement or next(
        (
            s.strip()
            for s in sentences
            if re.search(r"graduat|class of|batch of", s, re.I) and re.search(r"20\d{2}", s)
        ),
        None,
    )
    job.experience_requirement = job.experience_requirement or next(
        (s.strip() for s in sentences if re.search(r"\d+\+?\s+years?.{0,30}experience", s, re.I)), None
    )
    return job


def extract(page, url):
    soup = BeautifulSoup(page.get("rawHtml", ""), "html.parser")
    postings = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            postings.extend(nodes(json.loads(script.string or script.get_text())))
        except (TypeError, ValueError):
            continue
    # A list page with multiple jobs is not evidence for a single opportunity.
    if len(postings) != 1:
        return None
    data = postings[0]
    org = data.get("hiringOrganization") or {}
    company = org.get("name") if isinstance(org, dict) else None
    title = data.get("title")
    if not isinstance(title, str) or not isinstance(company, str):
        return None
    application = None
    for link in soup.find_all("a", href=True):
        if re.search(r"\bapply\b", link.get_text(" ", strip=True), re.I):
            application = urljoin(url, link["href"])
            break
    application_form = any(
        re.search(
            r"apply|application", " ".join(str(form.get(key, "")) for key in ("id", "name", "action")), re.I
        )
        for form in soup.find_all("form")
    )
    if application is None and (data.get("directApply") is True or application_form):
        application = url
    remote = True if data.get("jobLocationType") == "TELECOMMUTE" else None
    location = location_text(data.get("jobLocation"))
    eligible_regions = location_text(data.get("applicantLocationRequirements"))
    if eligible_regions:
        location = f"{location or 'Remote'}; eligible: {eligible_regions}"
    job = Job(
        title=plain(title),
        company=plain(company),
        source_url=url,
        application_url=application,
        location=location,
        remote=remote,
        employment_type=plain(data.get("employmentType")),
        description=plain(data.get("description")) or "",
        requirements=[
            plain(data[k]) for k in ("qualifications", "skills", "educationRequirements") if data.get(k)
        ],
        experience_requirement=plain(data.get("experienceRequirements")),
        salary_or_stipend=plain(data.get("baseSalary")),
        deadline=plain(data.get("validThrough")),
        posted_date=plain(data.get("datePosted")),
        evidence={
            "structured_job": True,
            "fetched_at": page.get("metadata", {}).get("fetchedAt"),
            "page_status": page.get("metadata", {}).get("statusCode"),
            "final_url": page.get("metadata", {}).get("url", url),
            "applicant_locations": eligible_regions,
        },
    )
    return enrich(job)
