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


@pytest.mark.parametrize(
    "endpoint,payload",
    [
        ("crawl", {"url": "https://example.com"}),
        ("agent", {"prompt": "Read every page"}),
        ("parse", {"url": "https://example.com/document.pdf"}),
        (
            "search",
            {"query": "intern", "limit": 10, "sources": ["web"], "scrapeOptions": {"parsers": ["pdf"]}},
        ),
        (
            "scrape",
            {
                "url": "https://example.com/job",
                "formats": ["markdown", "rawHtml"],
                "onlyMainContent": False,
                "maxAge": 0,
                "parsers": ["pdf"],
                "proxy": "basic",
            },
        ),
    ],
)
def test_expensive_request_shapes_blocked_before_network(endpoint, payload):
    def no_network(request):
        pytest.fail("Unsafe paid request must never reach provider")

    with pytest.raises(ProviderError, match="safety policy"):
        service(no_network)._call(endpoint, payload)


def test_unexpected_charge_stops_further_paid_requests():
    from app.services.credit_budget import BudgetExceeded

    calls = []

    class Budget:
        def reserve(self, endpoint, cost):
            return 1

        def finish(self, reservation, reported, request_id):
            assert reported == 5

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"success": True, "creditsUsed": 5, "data": {"web": []}})

    api = service(handler)
    api.budget = Budget()
    api.account_available = 250
    api.search("internship")
    with pytest.raises(BudgetExceeded):
        api.search("another internship")
    assert len(calls) == 1
