import time

import httpx


class ProviderError(RuntimeError):
    """Sanitized error: never includes request headers or provider response bodies."""


class Http:
    def __init__(self, client=None, sleep=time.sleep):
        self.client = client or httpx.Client(timeout=75, follow_redirects=False)
        self.sleep = sleep

    def json(self, method, url, *, attempts=3, **kwargs):
        for attempt in range(attempts):
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError:
                if attempt == attempts - 1:
                    raise ProviderError("Network request failed") from None
                self.sleep(2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < attempts - 1:
                    delay = response.headers.get("Retry-After", "")
                    self.sleep(min(float(delay), 30) if delay.isdigit() else 2**attempt)
                    continue
            if not 200 <= response.status_code < 300:
                raise ProviderError(f"Provider HTTP {response.status_code}")
            try:
                data = response.json()
            except ValueError:
                raise ProviderError("Provider returned invalid JSON") from None
            if not isinstance(data, (dict, list)):
                raise ProviderError("Provider returned invalid structure")
            return data
        raise ProviderError("Retry budget exhausted")
