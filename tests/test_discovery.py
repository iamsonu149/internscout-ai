import httpx
import pytest

from app.services.firecrawl_service import Firecrawl, queries_for
from app.services.http import Http, ProviderError


def service(handler):
    return Firecrawl(
        "test-key", Http(httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)
    )


def test_search_contract():
    def handler(request):
        assert request.url.path == "/v2/search"
        assert b"scrapeOptions" not in request.content
        return httpx.Response(200, json={"success": True, "data": {"web": [{"url": "https://a.com/job"}]}})

    assert len(service(handler).search("python intern")) == 1


def test_retry_and_malformed():
    count = []

    def handler(request):
        count.append(1)
        return httpx.Response(429)

    with pytest.raises(ProviderError, match="429"):
        service(handler).search("intern")
    assert len(count) == 3
    with pytest.raises(ProviderError, match="Malformed"):
        service(lambda _: httpx.Response(200, json={"success": True, "data": []})).search("intern")


def test_query_rotation():
    profile = {
        "primary_roles": ["Backend Intern"],
        "secondary_roles": ["ML Intern"],
        "country": "India",
        "graduation_year": 2027,
    }
    first = queries_for(profile)
    assert len(first) == len(set(first))
    assert queries_for(profile, 1)[0] == first[1]
