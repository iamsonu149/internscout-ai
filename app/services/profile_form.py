"""Translate ordinary form fields into the same validated profile as AI imports."""

import json
import re

from app.services.resume_profile import SCHEMA, parse_profile


def profile_from_form(form):
    def read(spec, name):
        kind = spec.get("type")
        if kind == "object":
            return {
                key: read(value, f"{name}.{key}" if name else key)
                for key, value in spec["properties"].items()
            }
        if kind == "array":
            if spec["items"].get("type") == "object":
                result = []
                for i in range(spec["maxItems"]):
                    prefix = f"{name}.{i}"
                    if any(
                        key.startswith(prefix + ".") and str(value).strip() for key, value in form.items()
                    ):
                        result.append(read(spec["items"], prefix))
                return result
            values = [v.strip() for v in re.split(r"[,\n]", form.get(name, "")) if v.strip()]
            return [float(v) for v in values] if spec["items"].get("type") == "number" else values
        value = form.get(name, "").strip()
        if name == "schema_version":
            return 1
        if not value:
            return None
        if isinstance(kind, list) and "boolean" in kind:
            if value not in ("yes", "no"):
                raise ValueError("Choose Yes, No or Not specified.")
            return value == "yes"
        if isinstance(kind, list) and "integer" in kind:
            return int(value)
        if isinstance(kind, list) and "number" in kind:
            return float(value)
        return value

    return parse_profile(json.dumps(read(SCHEMA, "")))
