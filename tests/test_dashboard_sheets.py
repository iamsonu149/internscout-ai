import pytest

from app.config import Settings
from app.dashboard import create_app
from app.services.dashboard_feed import SheetDashboardFeed, parse_rows
from app.services.google_sheets import HEADERS, RejectedSheets, sheet_row
from app.services.http import ProviderError
from tests.test_sheets import item


def test_parse_sheet_rows_never_invents_timestamps_or_unsafe_links():
    row = sheet_row(item())
    row[15:19] = ["javascript:alert(1)", "https://example.com/job", "APPLIED", "My own note"]
    row[6] = "nan"
    parsed = parse_rows([HEADERS, row], HEADERS)[0]
    assert parsed["application_url"] is None
    assert parsed["match"]["match_score"] == 0
    assert parsed["last_seen"] is None
    assert parsed["status"] == "APPLIED" and parsed["notes"] == "My own note"
    with pytest.raises(ProviderError):
        parse_rows([["wrong headers"]], HEADERS)


def test_feed_caches_refreshes_and_preserves_snapshot_on_error(monkeypatch):
    state = {"calls": 0, "fail": False}
    row = sheet_row(item())

    class FakeSheet:
        session = object()

        def __init__(self, *args, **kwargs):
            pass

        def read_rows(self):
            state["calls"] += 1
            if state["fail"]:
                raise RuntimeError("secret-bearing provider message")
            return [HEADERS, row]

    class FakeRejected(FakeSheet):
        headers = RejectedSheets.headers

        def read_rows(self):
            return [self.headers]

    monkeypatch.setattr("app.services.dashboard_feed.RejectedSheets", FakeRejected)
    feed = SheetDashboardFeed(Settings(), factory=FakeSheet, clock=lambda: 0)
    assert len(feed.read()["jobs"]) == 1
    feed.read()
    assert state["calls"] == 1
    row[17] = "SAVED"
    assert feed.read(force=True)["jobs"][0]["status"] == "SAVED"
    state["fail"] = True
    snapshot = feed.read(force=True)
    assert snapshot["error"] and len(snapshot["jobs"]) == 1
    assert "secret-bearing" not in snapshot["error"]


def test_dashboard_displays_cloud_results_and_audit_separately(tmp_path):
    accepted = sheet_row(item())
    accepted[17:19] = ["APPLIED", "Sheet note <script>bad</script>"]
    rejected = sheet_row(item()) + ["Requires graduation in 2026", "EXCLUDED", "2026-09-25T01:00:00Z"]
    rejected[2] = "Python Intern excluded"

    class Feed:
        forced = False

        def read(self, force=False):
            self.forced = force
            return {
                "jobs": parse_rows([HEADERS, accepted], HEADERS),
                "rejected": parse_rows([RejectedSheets.headers, rejected], RejectedSheets.headers, True),
                "synced_at": "2026-09-25T02:00:00Z",
                "error": None,
            }

    feed = Feed()
    client = create_app(
        Settings(database_path=str(tmp_path / "db"), sheet_id="test"), sheet_feed=feed
    ).test_client()
    page = client.get("/?refresh=1")
    assert page.status_code == 200 and feed.forced
    assert b"APPLIED" in page.data and b"Sheet note &lt;script&gt;" in page.data
    assert b"Python Intern excluded" not in page.data
    assert b"Refresh from Sheets" in page.data and b"Update local tracking" not in page.data
    assert b'href="/?view=ALL&amp;refresh=1"' in page.data
    assert b'href="/?view=SCREENED"' in page.data
    page = client.get("/?view=SCREENED")
    assert page.status_code == 200 and b"Requires graduation in 2026" in page.data
    with client.session_transaction() as session:
        csrf = session["csrf"]
    assert client.post("/jobs/2/tracking", data={"csrf": csrf, "status": "SAVED"}).status_code == 409


def test_connection_failure_is_visible_not_empty_success(tmp_path):
    class Feed:
        def read(self, force=False):
            return {"jobs": [], "rejected": [], "synced_at": None, "error": "Could not refresh Google Sheets"}

    client = create_app(
        Settings(database_path=str(tmp_path / "db"), sheet_id="test"), sheet_feed=Feed()
    ).test_client()
    assert b"Could not refresh Google Sheets" in client.get("/").data
