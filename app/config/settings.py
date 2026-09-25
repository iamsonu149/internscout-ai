import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    firecrawl_api_key: str = field(default="", repr=False)
    database_path: str = "data/internscout.db"
    profile_path: str = "profile.json"
    sources_path: str = "sources.json"
    max_queries: int = 3
    results_per_query: int = 5
    max_scrapes: int = 8
    min_match_score: int = 60
    recheck_days: int = 7
    sheet_id: str = ""
    sheet_tab: str = "Opportunities"
    rejected_sheet_tab: str = "Rejected Matches"
    google_credentials_file: str = ""
    google_credentials_json: str = field(default="", repr=False)
    google_oauth_file: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_model: str = ""
    llm_base_url: str = ""
    max_llm_calls: int = 0

    @classmethod
    def from_env(cls):
        load_dotenv()
        names = {
            "sheet_id": "GOOGLE_SHEET_ID",
            "sheet_tab": "GOOGLE_SHEET_TAB",
            "rejected_sheet_tab": "GOOGLE_REJECTED_SHEET_TAB",
            "google_credentials_file": "GOOGLE_APPLICATION_CREDENTIALS",
        }
        integers = {
            "max_queries",
            "results_per_query",
            "max_scrapes",
            "min_match_score",
            "recheck_days",
            "max_llm_calls",
        }
        defaults = cls()
        values = {}
        for name in cls.__dataclass_fields__:
            value = os.getenv(names.get(name, name.upper()), getattr(defaults, name))
            values[name] = int(value) if name in integers else value
        obj = cls(**values)
        if not obj.rejected_sheet_tab.strip() or obj.rejected_sheet_tab == obj.sheet_tab:
            raise ValueError("Accepted and rejected sheet tabs must have distinct names")
        if not (
            0 <= obj.max_queries <= 20
            and 1 <= obj.results_per_query <= 20
            and 0 <= obj.max_scrapes <= 100
            and 0 <= obj.max_llm_calls <= 30
            and 0 <= obj.min_match_score <= 100
            and obj.recheck_days >= 1
        ):
            raise ValueError("Invalid budget, score, or recheck configuration")
        Path(obj.database_path).parent.mkdir(parents=True, exist_ok=True)
        return obj
