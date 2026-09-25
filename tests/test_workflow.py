from pathlib import Path

import yaml


def test_daily_workflow_security_schedule_and_persistence():
    doc = yaml.load(Path(".github/workflows/job_search.yml").read_text(), Loader=yaml.BaseLoader)
    assert doc["on"]["schedule"][0]["cron"] == "30 3 * * *"
    assert doc["permissions"] == {"contents": "read"}
    assert doc["concurrency"]["cancel-in-progress"] == "false"
    steps = doc["jobs"]["discover"]["steps"]
    assert any(step.get("run") == "python main.py search" for step in steps)
    assert any(step.get("uses") == "actions/cache/save@v4" and step.get("if") == "always()" for step in steps)
