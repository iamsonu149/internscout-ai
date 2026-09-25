"""Small opt-in live checks. Reads Sheets without modifying it; prints no credentials."""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.services.firecrawl_service import Firecrawl  # noqa: E402
from app.services.google_sheets import GoogleSheets  # noqa: E402
from app.services.job_extractor import extract  # noqa: E402
from app.services.verifier import ATS_HOSTS  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firecrawl", action="store_true")
    parser.add_argument("--sheets", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    results = {}
    failed = False
    if args.firecrawl:
        if not settings.firecrawl_api_key:
            results["firecrawl"] = "SKIPPED: missing FIRECRAWL_API_KEY"
        else:
            try:
                provider = Firecrawl(settings.firecrawl_api_key)
                hits = provider.search('site:jobs.lever.co "software intern" India', limit=2)
                results["firecrawl_search"] = {"status": "PASS", "results": len(hits)}
                url = next((h["url"] for h in hits if urlsplit(h["url"]).hostname in ATS_HOSTS), None)
                if url:
                    page = provider.scrape(url)
                    results["firecrawl_scrape"] = {
                        "status": "PASS",
                        "structured_job_found": extract(page, url) is not None,
                    }
                else:
                    results["firecrawl_scrape"] = "SKIPPED: no ATS URL returned"
            except Exception as exc:
                results["firecrawl"] = f"FAILED: {type(exc).__name__} (details redacted)"
                failed = True
    if args.sheets:
        if not settings.sheet_id:
            results["sheets"] = "SKIPPED: missing GOOGLE_SHEET_ID"
        else:
            try:
                sheets = GoogleSheets(settings)
                data = sheets.call("GET", "", params={"fields": "sheets.properties.title"})
                exists = any(s["properties"]["title"] == settings.sheet_tab for s in data.get("sheets", []))
                if exists:
                    sheets.call("GET", "/values/" + quote(sheets.tab + "!A1:S1", safe=""))
                results["sheets"] = {"status": "PASS (read only)", "tab_exists": exists}
            except Exception as exc:
                results["sheets"] = f"FAILED: {type(exc).__name__} (details redacted)"
                failed = True
    print(json.dumps(results, indent=2))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
