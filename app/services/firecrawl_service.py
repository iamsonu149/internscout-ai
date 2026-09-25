import math
import re
from urllib.parse import unquote

from app.services.credit_budget import BudgetExceeded
from app.services.http import Http, ProviderError


def document_url(url):
    decoded = unquote(unquote(url)).lower()
    return bool(re.search(r"\.(?:pdf|docx?|xlsx?|pptx?|csv|zip)(?:$|[/?&#;=])", decoded))


def queries_for(profile, day_index=0):
    roles = profile["primary_roles"] + profile["secondary_roles"]
    queries = [f'"{role}" {profile["country"]} {profile["graduation_year"]}' for role in roles]
    queries += [f'"{role}" remote {profile["country"]}' for role in profile["primary_roles"][:3]]
    # Many valid postings omit a graduation year and use city names instead of India.
    queries += [
        f'"{role}" internship {city}'
        for role in ("software", "backend", "python")
        for city in ("Bengaluru", "Hyderabad", "Pune")
    ]
    queries += [
        f"site:{domain} internship software {profile['country']}"
        for domain in ("boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com")
    ]
    queries = list(dict.fromkeys(queries))
    offset = day_index % len(queries)
    return queries[offset:] + queries[:offset]


class Firecrawl:
    def __init__(self, key, http=None, budget=None):
        self.key = key
        self.http = http or Http()
        self.budget = budget
        self.account_available = None

    def account_credits(self):
        result = self.http.json(
            "GET",
            "https://api.firecrawl.dev/v2/team/credit-usage",
            headers={"Authorization": f"Bearer {self.key}"},
            attempts=1,
        )
        value = (result.get("data") or {}).get("remainingCredits")
        if result.get("success") is not True or type(value) is not int or value < 0:
            raise ProviderError("Credit balance could not be verified")
        self.account_available = value
        return value

    def _call(self, endpoint, payload):
        if not self.key:
            raise ProviderError("FIRECRAWL_API_KEY is not configured")
        cost = 2 * math.ceil(payload["limit"] / 10) if endpoint == "search" else 1
        reservation = None
        if self.budget:
            if self.account_available is None:
                self.account_credits()
            if self.account_available < cost:
                raise BudgetExceeded("Insufficient verified account credit balance")
            reservation = self.budget.reserve(endpoint, cost)
            self.account_available -= cost
        result = self.http.json(
            "POST",
            f"https://api.firecrawl.dev/v2/{endpoint}",
            headers={"Authorization": f"Bearer {self.key}"},
            json=payload,
            attempts=1,  # A timed-out paid POST may already have consumed credits.
        )
        if not isinstance(result, dict) or result.get("success") is not True:
            raise ProviderError("Firecrawl request was unsuccessful")
        if reservation is not None:
            data = result.get("data") or {}
            metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
            reported = result.get("creditsUsed", metadata.get("creditsUsed"))
            self.budget.finish(reservation, reported, result.get("id") or metadata.get("scrapeId"))
            if type(reported) is int and reported > cost:
                self.account_available -= reported - cost
        return result.get("data")

    def search(self, query, limit=5):
        data = self._call(
            "search", {"query": query + " -filetype:pdf -filetype:docx", "limit": limit, "sources": ["web"]}
        )
        if not isinstance(data, dict) or not isinstance(data.get("web"), list):
            raise ProviderError("Malformed Firecrawl search response")
        return [
            item
            for item in data["web"]
            if isinstance(item, dict) and isinstance(item.get("url"), str) and not document_url(item["url"])
        ]

    def scrape(self, url):
        if document_url(url):
            raise ProviderError("Document URLs are blocked before spending scrape credits")
        data = self._call(
            "scrape",
            {
                "url": url,
                "formats": ["markdown", "rawHtml"],
                "onlyMainContent": False,
                "maxAge": 0,
                "parsers": [],
                "proxy": "basic",
            },
        )
        if not isinstance(data, dict) or not (data.get("rawHtml") or data.get("markdown")):
            raise ProviderError("Missing job page content")
        metadata = data.get("metadata") or {}
        if (
            "pdf" in str(metadata.get("contentType", "")).lower()
            or metadata.get("numPages")
            or document_url(str(metadata.get("url", "")))
        ):
            raise ProviderError("Document response rejected; PDF parsing is disabled")
        status = metadata.get("statusCode")
        if not isinstance(status, int) or not (200 <= status < 300 or status == 304):
            raise ProviderError("Job page is unavailable")
        return data
