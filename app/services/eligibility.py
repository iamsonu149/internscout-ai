import re
from dataclasses import dataclass
from datetime import date

from app.services.compensation import PAY_REASON, PAY_UNCONFIRMED, compensation_allowed, paid_evidence


@dataclass
class Eligibility:
    accepted: bool
    concerns: list[str]
    reasons: list[str]


def evaluate(job, profile, today=None):
    today = today or date.today()
    text = " ".join(
        [
            job.description,
            *job.requirements,
            job.graduation_requirement or "",
            job.experience_requirement or "",
        ]
    )
    title = job.title.lower()
    reasons, concerns = [], []
    if not compensation_allowed(job.salary_or_stipend, job.description):
        reasons.append(PAY_REASON)
    elif not paid_evidence(job.salary_or_stipend, job.description):
        concerns.append(PAY_UNCONFIRMED)
    if not re.search(r"\bintern(?:ship)?\b", title + " " + (job.employment_type or ""), re.I):
        reasons.append("Not explicitly an internship")
    if not re.search(
        r"software|backend|back.end|front[ -]?end|full[ -]?stack|web develop|mobile develop|python|\bsde\b|machine learning|data scien|data engineer|data analy|ai/ml|\bml\b|\bqa\b|quality assurance|test engineer|devops|cloud engineer|cybersecurity",
        title,
    ):
        reasons.append("Outside target technical roles")
    if re.search(r"senior|staff|principal|sales|recruit|marketing", title):
        reasons.append("Role is unrelated or too senior")
    location = (job.location or "").lower()
    india = bool(
        re.search(
            r"\bindia\b|\bbangalore\b|\bbengaluru\b|\bhyderabad\b|\bpune\b|\bchennai\b|\bmumbai\b|\bdelhi\b|\bgurugram\b|\bgurgaon\b|\bnoida\b|\bkolkata\b|\bahmedabad\b|\bkochi\b",
            location,
        )
    )
    global_remote = job.remote is True and bool(
        re.search(r"worldwide|anywhere|all countries|global", location)
    )
    if not india and not global_remote:
        reasons.append("India eligibility is not established by the location")
    applicant_locations = job.evidence.get("applicant_locations")
    if applicant_locations and not re.search(
        r"\bindia\b|\bIN\b|worldwide|all countries", applicant_locations, re.I
    ):
        reasons.append("Explicit applicant location requirements do not include India")
    if re.search(
        r"(?:US|USA|UK|EU|United States)[ -]only|(?:must|need to)\s+(?:be\s+)?(?:based|reside|located)\s+in\s+(?:the\s+)?(?:US\b|USA\b|United States|UK\b|Europe)|not (?:available|open) (?:to|in) India",
        text + " " + location,
        re.I,
    ):
        reasons.append("Explicit geographic restriction")
    if job.deadline:
        try:
            if date.fromisoformat(job.deadline[:10]) < today:
                reasons.append("Application deadline has passed")
        except (ValueError, TypeError):
            reasons.append("Deadline cannot be reliably interpreted")
    if re.search(
        r"no longer accepting|position (?:has been |is )?filled|job (?:has )?expired|applications (?:are )?closed|no longer available",
        text,
        re.I,
    ):
        reasons.append("Listing says applications are closed")
    grad = job.graduation_requirement or ""
    years = [int(y) for y in re.findall(r"\b20\d{2}\b", grad)]
    target = profile["graduation_year"]
    if years:
        allowed = target in years
        if len(years) == 2 and re.search(r"between|\bto\b|[-–]", grad):
            allowed = min(years) <= target <= max(years)
        if not allowed:
            reasons.append(f"Graduation requirement does not establish eligibility for {target}")
    else:
        concerns.append("Graduation cohort not stated; confirm before applying")
    experience = job.experience_requirement or ""
    if re.search(r"\b(?:[2-9]|\d{2,})\s*(?:\+|[-–]\s*\d+)?\s+years?", experience, re.I) and not re.search(
        r"prefer|optional|nice.to.have", experience, re.I
    ):
        reasons.append("Requires multiple years of experience not established by profile")
    elif experience:
        concerns.append("Confirm experience requirement: " + experience)
    if re.search(r"\b(?:Ph\.?D|doctoral|master'?s)\b", text, re.I) and not re.search(
        r"bachelor|undergraduate|\bBS\b|\bBSc\b|or equivalent", text, re.I
    ):
        reasons.append("Graduate-level education required or ambiguous")
    if not job.deadline:
        concerns.append("No deadline published; availability can change")
    # Specific degree labels are not interchangeable with the user's BS Data Science.
    if re.search(r"(?:b\.?tech|b\.?e\.?|bachelor of (?:technology|engineering)).{0,35}\bonly\b", text, re.I):
        if not re.search(
            r"B\.?Tech|Bachelor of (?:Technology|Engineering)", profile.get("education", ""), re.I
        ):
            reasons.append("Restricted to BTech/BE; profile lists a BS degree")
    elif re.search(r"computer science|computer engineering", text, re.I) and not re.search(
        r"data science|related (?:field|discipline)|equivalent", text, re.I
    ):
        concerns.append("Degree wording names CS/Computer Engineering; confirm BS Data Science eligibility")
    availability = profile.get("availability", {})
    if (
        re.search(r"full[ -]time", text + " " + (job.employment_type or ""), re.I)
        and availability.get("full_time") is False
    ):
        reasons.append("Full-time commitment conflicts with availability")
    hours = re.search(r"(\d{1,2})\s*hours?\s*(?:per|a|/)\s*day", text, re.I)
    if (
        hours
        and availability.get("max_hours_per_day") is not None
        and int(hours[1]) > availability["max_hours_per_day"]
    ):
        reasons.append("Daily hours exceed stated availability")
    duration = re.search(r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s*months?", text, re.I)
    if (
        duration
        and availability.get("max_months") is not None
        and int(duration[1]) > availability["max_months"]
    ):
        reasons.append("Required duration exceeds availability")
    start = re.search(
        r"(?:start(?:ing)? date|starts? on|start from)\s*:?\s*(20\d{2}-\d{2}-\d{2})", text, re.I
    )
    if start:
        try:
            start_date = date.fromisoformat(start[1])
            available_from = availability.get("available_from")
            if available_from and start_date < date.fromisoformat(available_from):
                reasons.append("Required start date is before availability")
        except ValueError:
            concerns.append("Start date could not be interpreted reliably")
    else:
        concerns.append("Confirm start date with employer")
    return Eligibility(not reasons, concerns, reasons)
