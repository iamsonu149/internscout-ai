import re

from app.models import Job
from app.services.http import Http, ProviderError
from app.services.job_extractor import enrich, plain


class PublicATS:
    def __init__(self, http=None):
        self.http = http or Http()

    def fetch(self, board):
        kind, slug, company = board["type"], board["slug"], board["company"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", slug):
            raise ValueError("Invalid ATS board slug")
        if kind == "greenhouse":
            data = self.http.json(
                "GET", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}
            )
            rows = data.get("jobs") if isinstance(data, dict) else None
        elif kind == "lever":
            data = self.http.json("GET", f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
            rows = data
        elif kind == "ashby":
            data = self.http.json("GET", f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
            rows = data.get("jobs") if isinstance(data, dict) else None
        else:
            raise ValueError("Unsupported ATS type")
        if not isinstance(rows, list):
            raise ProviderError("Malformed ATS job list")
        jobs = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                job = self._normalize(kind, row, company)
                if job:
                    job.evidence = {
                        "public_api": True,
                        "board": slug,
                        "active": True,
                        "company_mapping": company,
                    }
                    jobs.append(enrich(job))
            except (TypeError, KeyError, AttributeError):
                continue
        return jobs

    def _normalize(self, kind, row, company):
        if kind == "greenhouse":
            if row.get("internal_job_id") is None:
                return None
            return Job(
                title=row["title"],
                company=company,
                source_url=row["absolute_url"],
                application_url=row["absolute_url"],
                location=row.get("location", {}).get("name"),
                description=plain(row.get("content")) or "",
                source_type=kind,
                posted_date=row.get("first_published"),
                deadline=row.get("application_deadline"),
            )
        if kind == "lever":
            categories = row.get("categories", {})
            details = " ".join(plain(item.get("content")) or "" for item in row.get("lists", []))
            return Job(
                title=row["text"],
                company=company,
                source_url=row["hostedUrl"],
                application_url=row.get("applyUrl"),
                location=categories.get("location"),
                employment_type=categories.get("commitment"),
                description=(plain(row.get("descriptionPlain") or row.get("description")) or "")
                + " "
                + details,
                remote=row.get("workplaceType") == "remote" if row.get("workplaceType") else None,
                source_type=kind,
            )
        if row.get("isListed") is False:
            return None
        return Job(
            title=row["title"],
            company=company,
            source_url=row["jobUrl"],
            application_url=row.get("applyUrl"),
            location=row.get("location"),
            employment_type=row.get("employmentType"),
            remote=row.get("isRemote"),
            description=plain(row.get("descriptionPlain") or row.get("descriptionHtml")) or "",
            source_type=kind,
            posted_date=row.get("publishedAt"),
        )
