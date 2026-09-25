"""Bounded direct HTML checks; no Firecrawl credits and no blind redirect following."""

import re
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.services.deduplicator import normalize_url
from app.services.firecrawl_service import document_url


def check_closure(url, allowed_hosts, client=None):
    try:
        url = normalize_url(url)
        with httpx.Client(timeout=15, follow_redirects=False) if client is None else _borrow(client) as http:
            for _ in range(3):
                if urlsplit(url).hostname not in allowed_hosts or document_url(url):
                    return "UNKNOWN"
                with http.stream(
                    "GET", url, headers={"User-Agent": "InternScout/1.0 (job availability check)"}
                ) as response:
                    if response.status_code in (404, 410):
                        return "CLOSED"
                    if response.status_code in (301, 302, 303, 307, 308):
                        destination = normalize_url(urljoin(url, response.headers.get("location", "")))
                        # Redirect to a board/home page is inconclusive, not proof of closure.
                        if urlsplit(destination).path in ("", "/"):
                            return "UNKNOWN"
                        url = destination
                        continue
                    if response.status_code != 200 or "text/html" not in response.headers.get(
                        "content-type", ""
                    ):
                        return "UNKNOWN"
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 2_000_000:
                            return "UNKNOWN"
                    soup = BeautifulSoup(bytes(content), "html.parser")
                    for element in soup(["script", "style", "nav", "footer"]):
                        element.decompose()
                    for element in soup.select('[hidden], [aria-hidden="true"], template'):
                        element.decompose()
                    text = soup.get_text(" ", strip=True)
                    if re.search(
                        r"(?:this|the) (?:job|position|role) (?:is|has been) (?:closed|filled|no longer available)|no longer accepting applications|job (?:has )?expired",
                        text,
                        re.I,
                    ):
                        if re.search(r"\bapply now\b|\bsubmit application\b", text, re.I):
                            return "UNKNOWN"
                        return "CLOSED"
                    return "UNKNOWN"
    except (httpx.HTTPError, ValueError):
        return "UNKNOWN"
    return "UNKNOWN"


class _borrow:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *args):
        return False
