"""Convert a reviewed resume profile without borrowing the owner's defaults."""

import json
import re

from app.services.resume_profile import parse_profile

ROLE_PATTERNS = [
    r"software|\bsde\b",
    r"back.?end|python",
    r"front.?end",
    r"full.?stack",
    r"data scien|machine learning|ai/ml|\bml\b|\bai\b",
    r"data engineer",
    r"data analy",
    r"\bqa\b|quality assurance|test engineer",
    r"devops|cloud",
    r"cyber|security",
    r"mobile|android|ios",
]


def preferred_role(title, roles):
    target = " ".join(roles)
    patterns = [pattern for pattern in ROLE_PATTERNS if re.search(pattern, target, re.I)]
    return any(re.search(pattern, title, re.I) for pattern in patterns)


def matching_profile(document):
    document = parse_profile(json.dumps(document))
    pref = document.get("preferences_to_confirm", {})
    years = {e.get("graduation_year") for e in document.get("education", []) if e.get("graduation_year")}
    if len(years) != 1 or not document.get("skills") or not pref.get("desired_roles"):
        raise ValueError(
            "Add skills, one target graduation year and confirmed desired roles to your profile."
        )
    if not any(str(c).lower() == "india" for c in pref.get("countries_with_work_authorization", [])):
        raise ValueError(
            "This discovery pilot supports India and eligible worldwide remote roles. Confirm India work authorization in your profile to enable it."
        )
    if pref.get("minimum_monthly_stipend"):
        raise ValueError(
            "Exact minimum-stipend filtering is not available yet. Use paid/undisclosed-pay preferences for this pilot."
        )
    degrees = [
        " ".join(filter(None, [e.get("degree"), e.get("field_of_study")]))
        for e in document.get("education", [])
    ]
    return {
        "personalized": True,
        "skills": document["skills"],
        "graduation_year": years.pop(),
        "country": "India",
        "education": " / ".join(degrees),
        "primary_roles": pref["desired_roles"],
        "secondary_roles": [],
        "projects": [
            {"name": p.get("name") or "Project", "keywords": p.get("technologies", [])}
            for p in document.get("projects", [])
        ],
        "experience": [e.get("summary") or "" for e in document.get("experience", [])],
        "accept_undisclosed_compensation": pref.get("accept_undisclosed_compensation") is True,
        "preferred_locations": pref.get("preferred_locations", []),
        "remote_only": str(pref.get("remote_preference") or "").lower() in ("remote only", "remote-only"),
        "availability": {
            "full_time": pref.get("full_time_available"),
            "available_from": pref.get("earliest_start_date"),
            "max_months": max(pref.get("preferred_duration_months") or [0]) or None,
        },
    }
