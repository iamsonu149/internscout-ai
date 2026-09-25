import json
from dataclasses import replace

from app.database.repository import Repository
from app.services.eligibility import evaluate
from app.services.matcher import match_job, mentions
from tests.test_eligibility import job


def test_explainable_score_and_repository(tmp_path):
    profile = json.loads(open("profile.json", encoding="utf-8").read())
    item = job(
        description="Undergraduate backend intern: Python, Flask, PostgreSQL, REST APIs and Java. " * 3
    )
    item.verification_status = "VERIFIED_ATS"
    match = match_job(item, profile, evaluate(item, profile))
    assert match.match_score == sum(match.breakdown.values())
    assert "Java" in match.missing_skills and "Python" in match.matching_skills
    assert "Hospital Management Web Application" in match.relevant_projects
    repo = Repository(str(tmp_path / "test.db"))
    job_id, added = repo.upsert(item, match)
    assert added
    repo.update_tracking(job_id, "APPLIED", "Personal note")
    assert not repo.upsert(replace(item, application_url=item.application_url + "?utm_source=x"), match)[1]
    assert len(repo.jobs()) == 1
    assert repo.jobs()[0]["notes"] == "Personal note"
    assert repo.jobs()[0]["status"] == "APPLIED"


def test_skill_boundaries():
    assert mentions("Use C++ and PostgreSQL", "C++")
    assert not mentions("JavaScript", "Java")
