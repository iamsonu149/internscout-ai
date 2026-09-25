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
    assert len(count) == 1
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


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.lever.co/acme/report.pdf",
        "https://jobs.lever.co/acme/report.PDF?download=1",
        "https://jobs.lever.co/acme/report%252Epdf",
        "https://jobs.lever.co/download?file=report.pdf",
    ],
)
def test_documents_blocked_without_network(url):
    def no_network(request):
        pytest.fail("Document must be rejected before a paid call")

    with pytest.raises(ProviderError, match="blocked"):
        service(no_network).scrape(url)


def test_scrape_disables_pdf_parsing_and_proxy_upgrade():
    import json

    def handler(request):
        payload = json.loads(request.content)
        assert payload["parsers"] == []
        assert payload["proxy"] == "basic"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "markdown": "PDF response",
                    "metadata": {"statusCode": 200, "contentType": "application/pdf"},
                },
            },
        )

    with pytest.raises(ProviderError, match="Document response"):
        service(handler).scrape("https://jobs.lever.co/acme/opaque-id")


def test_paid_timeout_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("timeout")

    with pytest.raises(ProviderError):
        service(handler).search("internship")
    assert len(calls) == 1
