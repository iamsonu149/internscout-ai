import base64

import pytest

from app.config import Settings
from app.dashboard import create_app


class Feed:
    calls = 0

    def read(self, force=False):
        self.calls += 1
        return {"jobs": [], "rejected": [], "synced_at": "2026-09-25T00:00:00Z", "error": None}


def test_hosted_fails_closed_without_secrets():
    with pytest.raises(ValueError):
        create_app(Settings(dashboard_mode="hosted", sheet_id="test"))


def test_hosted_requires_login_before_sheet_access_and_no_sqlite(tmp_path):
    path = tmp_path / "must-not-exist.db"
    settings = Settings(
        dashboard_mode="hosted",
        sheet_id="test",
        database_path=str(path),
        dashboard_password="a" * 32,
        dashboard_secret_key="b" * 64,
    )
    feed = Feed()
    client = create_app(settings, sheet_feed=feed).test_client()
    base = "https://internscout-test.vercel.app"
    response = client.get("/", base_url=base)
    assert response.status_code == 401 and "Basic" in response.headers["WWW-Authenticate"]
    assert feed.calls == 0 and not path.exists()
    token = base64.b64encode(f"internscout:{settings.dashboard_password}".encode()).decode()
    response = client.get("/", base_url=base, headers={"Authorization": f"Basic {token}"})
    assert response.status_code == 200 and feed.calls == 1
    assert "Secure" in response.headers["Set-Cookie"]
    assert not path.exists()
    assert (
        client.get(
            "/", base_url="https://evil.example", headers={"Authorization": f"Basic {token}"}
        ).status_code
        == 400
    )
