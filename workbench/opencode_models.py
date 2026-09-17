"""Read only public model capability fields from OpenCode's verbose catalog."""
import json
import re


def variants(info):
    values = info.get("variants")
    if not isinstance(values, dict):
        return []
    return [key for key, value in values.items()
            if isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", key)
            and isinstance(value, dict) and not value.get("disabled")]


def parse_catalog(text):
    rows = {}
    decoder = json.JSONDecoder()
    remaining = text
    while remaining.strip():
        line, _, remaining = remaining.lstrip().partition("\n")
        model = line.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*/[A-Za-z0-9][A-Za-z0-9._:/-]*", model):
            continue
        row = {"id": model, "name": model}
        remaining = remaining.lstrip()
        if remaining.startswith("{"):
            try:
                info, end = decoder.raw_decode(remaining)
            except ValueError:
                # Fail closed for reasoning capability, but keep the model usable.
                rows[model] = row
                continue
            remaining = remaining[end:]
            if isinstance(info, dict):
                if isinstance(info.get("name"), str):
                    row["name"] = f"{info['name']} · {model}"
                row["reasoning_efforts"] = variants(info)
        rows[model] = row
    return [rows[key] for key in sorted(rows)]
