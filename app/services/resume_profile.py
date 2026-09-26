"""Validate untrusted model output before it can become a saved profile."""

import json
import re
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

TEXT = {"type": ["string", "null"], "maxLength": 2000}
SHORT = {"type": ["string", "null"], "maxLength": 200}
BOOL = {"type": ["boolean", "null"]}
LIST = {"type": "array", "maxItems": 100, "items": {"type": "string", "maxLength": 200}}


def obj(properties):
    return {"type": "object", "properties": properties, "additionalProperties": False}


def rows(properties):
    return {"type": "array", "maxItems": 30, "items": obj(properties)}


SCHEMA = obj(
    {
        "schema_version": {"const": 1, "type": "integer"},
        "education": rows(
            {
                "institution": SHORT,
                "degree": SHORT,
                "field_of_study": SHORT,
                "graduation_year": {"type": ["integer", "null"], "minimum": 1950, "maximum": 2100},
                "graduation_status": {"enum": ["expected", "completed", None]},
            }
        ),
        "skills": LIST,
        "projects": rows({"name": SHORT, "summary": TEXT, "technologies": LIST}),
        "experience": rows(
            {
                "organization": SHORT,
                "role": SHORT,
                "type": SHORT,
                "start_date": SHORT,
                "end_date": SHORT,
                "currently_working": BOOL,
                "technologies": LIST,
                "summary": TEXT,
            }
        ),
        "current_city": SHORT,
        "current_country": SHORT,
        "github_url": SHORT,
        "portfolio_url": SHORT,
        "suggested_internship_roles": LIST,
        "preferences_to_confirm": obj(
            {
                "desired_roles": LIST,
                "earliest_start_date": SHORT,
                "full_time_available": BOOL,
                "preferred_duration_months": {
                    "type": "array",
                    "maxItems": 24,
                    "items": {"type": "number", "exclusiveMinimum": 0, "maximum": 36},
                },
                "preferred_locations": LIST,
                "remote_preference": SHORT,
                "willing_to_relocate": BOOL,
                "countries_with_work_authorization": LIST,
                "minimum_monthly_stipend": {"type": ["number", "null"], "minimum": 0, "maximum": 10000000},
                "stipend_currency": SHORT,
                "accept_undisclosed_compensation": BOOL,
            }
        ),
        "uncertainties_to_review": LIST,
    }
)
SCHEMA["required"] = ["schema_version", "skills"]

EMPTY_PROFILE = {
    "schema_version": 1,
    "education": [],
    "skills": [],
    "projects": [],
    "experience": [],
    "current_city": None,
    "current_country": None,
    "suggested_internship_roles": [],
    "preferences_to_confirm": {
        "desired_roles": [],
        "earliest_start_date": None,
        "full_time_available": None,
        "preferred_locations": [],
        "remote_preference": None,
        "accept_undisclosed_compensation": None,
    },
    "uncertainties_to_review": [],
}


def parse_profile(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 50000:
        raise ValueError("Profile must be smaller than 50 KB.")
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON fields are not allowed.")
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError("NaN and Infinity are not valid profile values.")

    try:
        document = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
        errors = list(Draft202012Validator(SCHEMA).iter_errors(document))
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("Paste a valid JSON profile, not the resume or instructions.") from None
    if errors:
        path = ".".join(str(part) for part in errors[0].absolute_path) or "profile"
        raise ValueError(f"Check the format of {path}. Use the supplied profile template.")
    for field in ("github_url", "portfolio_url"):
        value = document.get(field)
        if value:
            parts = urlsplit(value)
            if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
                raise ValueError(f"{field} must be a public HTTPS link.")
    return document
