import json

from app.services.job_extractor import extract


def test_structured_extraction_and_unknowns():
    payload = {
        "@type": "JobPosting",
        "title": "Python Intern",
        "hiringOrganization": {"name": "Acme"},
        "description": "<p>Build Flask APIs</p>",
        "jobLocation": {"address": {"addressCountry": "India"}},
    }
    page = {
        "rawHtml": '<script type="application/ld+json">'
        + json.dumps(payload)
        + '</script><a href="/apply">Apply now</a>'
    }
    job = extract(page, "https://acme.test/jobs/1")
    assert job.company == "Acme"
    assert job.location == "India"
    assert job.application_url == "https://acme.test/apply"
    assert job.deadline is None and job.remote is None


def test_snippets_and_multi_job_pages_not_extracted():
    assert extract({"markdown": "Great software internship at Google"}, "https://example.com") is None
    assert (
        extract({"rawHtml": '<script type="application/ld+json">invalid</script>'}, "https://example.com")
        is None
    )


def test_list_page_and_newsletter_form_not_application_evidence():
    posting = {
        "@type": "JobPosting",
        "title": "Backend Intern",
        "hiringOrganization": {"name": "Acme"},
        "description": "Python APIs",
    }
    page = {"rawHtml": '<script type="application/ld+json">' + json.dumps([posting, posting]) + "</script>"}
    assert extract(page, "https://example.com/jobs") is None
    page["rawHtml"] = (
        '<script type="application/ld+json">' + json.dumps(posting) + '</script><form id="newsletter"></form>'
    )
    assert extract(page, "https://example.com/job").application_url is None
