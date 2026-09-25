from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    title: str
    company: str
    source_url: str
    application_url: str | None = None
    location: str | None = None
    remote: bool | None = None
    employment_type: str | None = None
    description: str = ""
    requirements: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    graduation_requirement: str | None = None
    experience_requirement: str | None = None
    salary_or_stipend: str | None = None
    deadline: str | None = None
    posted_date: str | None = None
    source_type: str = "firecrawl"
    verification_status: str = "UNVERIFIED"
    verification_reasons: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    discovered_at: str = field(default_factory=utcnow)

    def to_dict(self):
        return asdict(self)


@dataclass
class Match:
    match_score: int
    matching_skills: list[str]
    missing_skills: list[str]
    relevant_projects: list[str]
    eligibility: str
    eligibility_concerns: list[str]
    reason: str
    breakdown: dict[str, int]
    ai_notes: str | None = None

    def to_dict(self):
        return asdict(self)
