"""Bounded scheduled worker; durable queue holds remaining tasks for the next run."""

import time

from app.config import Settings
from app.workspace_worker import work_once


def main():
    settings = Settings.from_env()
    deadline = time.monotonic() + 15 * 60
    while time.monotonic() < deadline:
        result = work_once(settings)
        print(result["state"], flush=True)
        if result["state"] == "IDLE":
            return


if __name__ == "__main__":
    main()
