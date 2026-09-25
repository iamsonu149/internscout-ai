from app.services.http import Http, ProviderError


def queries_for(profile, day_index=0):
    roles = profile["primary_roles"] + profile["secondary_roles"]
    queries = [f'"{role}" {profile["country"]} {profile["graduation_year"]}' for role in roles]
    queries += [f'"{role}" remote {profile["country"]}' for role in profile["primary_roles"][:3]]
    queries += [
        f"site:{domain} internship software {profile['country']}"
        for domain in ("boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com")
    ]
    queries = list(dict.fromkeys(queries))
    offset = day_index % len(queries)
    return queries[offset:] + queries[:offset]


class Firecrawl:
    def __init__(self, key, http=None):
        self.key = key
        self.http = http or Http()

    def _call(self, endpoint, payload):
        if not self.key:
            raise ProviderError("FIRECRAWL_API_KEY is not configured")
        result = self.http.json(
            "POST",
            f"https://api.firecrawl.dev/v2/{endpoint}",
            headers={"Authorization": f"Bearer {self.key}"},
            json=payload,
        )
        if not isinstance(result, dict) or result.get("success") is not True:
            raise ProviderError("Firecrawl request was unsuccessful")
        return result.get("data")

    def search(self, query, limit=5):
        data = self._call("search", {"query": query, "limit": limit, "sources": ["web"]})
        if not isinstance(data, dict) or not isinstance(data.get("web"), list):
            raise ProviderError("Malformed Firecrawl search response")
        return [item for item in data["web"] if isinstance(item, dict) and isinstance(item.get("url"), str)]

    def scrape(self, url):
        data = self._call(
            "scrape", {"url": url, "formats": ["markdown", "rawHtml"], "onlyMainContent": False, "maxAge": 0}
        )
        if not isinstance(data, dict) or not (data.get("rawHtml") or data.get("markdown")):
            raise ProviderError("Missing job page content")
        metadata = data.get("metadata") or {}
        status = metadata.get("statusCode")
        if not isinstance(status, int) or not (200 <= status < 300 or status == 304):
            raise ProviderError("Job page is unavailable")
        return data
