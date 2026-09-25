import hashlib
import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_url(url):
    if not isinstance(url, str):
        raise ValueError("Missing URL")
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise ValueError("Unsafe URL")
    if host == "localhost" or host.endswith((".local", ".internal")) or "." not in host:
        raise ValueError("Non-public URL")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP literal URL is not accepted")
    if parts.port not in (None, 80, 443):
        raise ValueError("Non-standard port")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower() not in {"source", "ref", "referrer", "fbclid", "gclid"}
    ]
    path = parts.path.rstrip("/") or "/"
    if host in {"jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com"}:
        path = re.sub(r"/(?:apply|application)$", "", path)
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        query = [(k, v) for k, v in query if k.lower() not in {"gh_jid", "gh_src"}]
    return urlunsplit((parts.scheme, host, path, urlencode(sorted(query)), ""))


def words(value):
    value = value.lower().replace("bengaluru", "bangalore").replace("internship", "intern")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def fingerprint(job):
    key = "|".join(words(v or "") for v in (job.company, job.title, job.location))
    return hashlib.sha256(key.encode()).hexdigest()
