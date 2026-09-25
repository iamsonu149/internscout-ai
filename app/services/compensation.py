"""Conservative pay evidence: company reputation and perks never establish pay."""

import json
import re


def paid_evidence(salary=None, description=""):
    text = "\n".join([str(salary or ""), description or ""])
    if re.search(
        r"\bunpaid\b|\bno\s+(?:salary|stipend|remuneration|pay)\b|without\s+(?:pay|stipend)|not\s+(?:a\s+)?paid|commission[ -]only",
        text,
        re.I,
    ):
        return None
    if re.search(
        r"(?:stipend|pay|salary).{0,45}(?:performance[ -]based|subject to|may be|not guaranteed|up to)|(?:performance[ -]based|discretionary|potential).{0,25}(?:stipend|pay|salary)",
        text,
        re.I,
    ):
        return None
    # Structured baseSalary, with a positive guaranteed lower bound and currency.
    try:
        data = json.loads(salary) if isinstance(salary, str) else salary
        if isinstance(data, dict) and data.get("currency"):
            value = data.get("value")
            if isinstance(value, dict):
                value = value.get("minValue", value.get("value"))
            if isinstance(value, (float, int)) and not isinstance(value, bool) and value > 0:
                return str(salary)
    except (ValueError, TypeError):
        pass
    for sentence in re.split(r"[\n;]|(?<=[.!?])\s+", text):
        if re.search(
            r"\bpaid\s+(?:technical\s+|summer\s+)?internship\b|\binternship\s+is\s+(?:fully\s+)?paid\b|\bstipend\s+(?:is\s+)?(?:provided|offered|paid)\b",
            sentence,
            re.I,
        ):
            return sentence.strip()[:300]
        if (
            re.search(r"\bstipend\b|\bsalary\b|\bcompensation\b", sentence, re.I)
            or sentence.strip() == str(salary or "").strip()
        ):
            amounts = re.findall(r"(?:INR|USD|EUR|GBP|Rs\.?|₹|\$|€|£)\s*([\d,]+(?:\.\d+)?)", sentence, re.I)
            if amounts and all(float(amount.replace(",", "")) > 0 for amount in amounts):
                return sentence.strip()[:300]
    return None


PAY_REASON = "Paid compensation is not confirmed, or the listing is unpaid/conditional"
