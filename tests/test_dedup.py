import pytest

from app.services.deduplicator import normalize_url


def test_urls_keep_job_identity():
    assert (
        normalize_url("https://jobs.example.com/role/?utm_source=x&id=1#apply")
        == "https://jobs.example.com/role?id=1"
    )
    assert normalize_url("https://x.com/job?id=1") != normalize_url("https://x.com/job?id=2")


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://localhost/a",
        "http://127.0.0.1",
        "https://user:pass@x.com",
        "https://x.com:8080/a",
    ],
)
def test_unsafe_urls(url):
    with pytest.raises(ValueError):
        normalize_url(url)
