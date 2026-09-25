import httpx
import pytest

from app.services.http import Http
from app.services.job_sources.ats import PublicATS


@pytest.mark.parametrize(
    "kind,payload",
    [
        (
            "greenhouse",
            {
                "jobs": [
                    {
                        "internal_job_id": 1,
                        "title": "Backend Intern",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                        "content": "<p>Python</p>",
                        "location": {"name": "India"},
                    }
                ]
            },
        ),
        (
            "lever",
            [
                {
                    "text": "Backend Intern",
                    "hostedUrl": "https://jobs.lever.co/acme/1",
                    "applyUrl": "https://jobs.lever.co/acme/1/apply",
                    "descriptionPlain": "Python",
                    "categories": {"location": "India"},
                }
            ],
        ),
        (
            "ashby",
            {
                "jobs": [
                    {
                        "title": "Backend Intern",
                        "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                        "applyUrl": "https://jobs.ashbyhq.com/acme/1/application",
                        "descriptionPlain": "Python",
                        "location": "India",
                    }
                ]
            },
        ),
    ],
)
def test_public_source_contracts(kind, payload):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    ats = PublicATS(Http(httpx.Client(transport=httpx.MockTransport(handler))))
    jobs = ats.fetch({"type": kind, "slug": "acme", "company": "Acme"})
    assert len(jobs) == 1
    assert jobs[0].company == "Acme" and jobs[0].location == "India"
    assert jobs[0].evidence["public_api"]
    assert all(request.method == "GET" for request in requests)
