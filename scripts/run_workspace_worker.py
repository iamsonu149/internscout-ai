"""Run this on a background-worker host, never inside a Vercel HTTP request."""

import logging
import time

from app.config import Settings
from app.workspace_worker import work_once


def main():
    settings = Settings.from_env()
    if settings.workspace_worker_enabled != "on":
        raise SystemExit("Set WORKSPACE_WORKER_ENABLED=on after database and credentials are ready.")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    while True:
        try:
            result = work_once(settings)
            logging.info("Worker state: %s", result["state"])
            time.sleep(60 if result["state"] == "IDLE" else 2)
        except KeyboardInterrupt:
            break
        except Exception:
            logging.error("Worker request failed; waiting before checking the queue again.")
            time.sleep(60)


if __name__ == "__main__":
    main()
