from app.config import Settings
from app.dashboard import create_app


def test_empty_dashboard_and_security(tmp_path):
    app = create_app(Settings(database_path=str(tmp_path / "ui.db")))
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert b"Make room for your next opportunity" in response.data
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert client.post("/jobs/1/tracking", data={"status": "APPLIED"}).status_code == 403
    assert client.get("/", headers={"Host": "evil.com"}).status_code == 400
    assert client.get("/health").json == {"status": "ok"}
