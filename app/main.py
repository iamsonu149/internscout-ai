import argparse
import json
import logging
import sys

from filelock import FileLock

from app.config import Settings
from app.database.repository import Repository
from app.services.google_sheets import GoogleSheets
from app.services.pipeline import Pipeline


def main():
    parser = argparse.ArgumentParser(description="InternScout AI — discovery and tracking, never auto-apply")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("search", help="Run bounded discovery and optionally sync Google Sheets")
    run.add_argument("--no-sync", action="store_true")
    run.add_argument("--max-queries", type=int)
    run.add_argument("--max-scrapes", type=int)
    sub.add_parser("sync", help="Retry Google Sheets sync without discovery")
    sub.add_parser("doctor", help="Report configuration readiness without displaying secrets")
    serve = sub.add_parser("dashboard", help="Serve local dashboard on loopback")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        settings = Settings.from_env()
        if args.command == "doctor":
            print(
                json.dumps(
                    {
                        "firecrawl_configured": bool(settings.firecrawl_api_key),
                        "sheet_configured": bool(settings.sheet_id),
                        "google_auth_configured": bool(
                            settings.google_credentials_json
                            or settings.google_credentials_file
                            or settings.google_oauth_file
                        ),
                        "external_ai_enabled": bool(settings.llm_api_key and settings.max_llm_calls),
                        "database": settings.database_path,
                    },
                    indent=2,
                )
            )
        elif args.command == "search":
            if args.max_queries is not None:
                if not 0 <= args.max_queries <= 20:
                    parser.error("--max-queries must be 0..20")
                settings.max_queries = args.max_queries
            if args.max_scrapes is not None:
                if not 0 <= args.max_scrapes <= 100:
                    parser.error("--max-scrapes must be 0..100")
                settings.max_scrapes = args.max_scrapes
            result = Pipeline(settings).run(sync=not args.no_sync)
            print(json.dumps(result, indent=2))
            return 0 if result["state"] == "COMPLETED" else 1
        elif args.command == "sync":
            if not settings.sheet_id:
                parser.error("GOOGLE_SHEET_ID is required")
            repo = Repository(settings.database_path)
            with FileLock(settings.database_path + ".lock", timeout=0):
                print(json.dumps(GoogleSheets(settings).sync(repo.jobs(), repo)))
        else:
            from app.dashboard import create_app

            create_app(settings).run(host="127.0.0.1", port=args.port, debug=False)
        return 0
    except Exception as exc:
        # Credential/provider exceptions can contain secrets. Deliberately omit their bodies.
        print(
            f"Operation failed ({type(exc).__name__}). Check configuration and structured logs.",
            file=sys.stderr,
        )
        return 1
