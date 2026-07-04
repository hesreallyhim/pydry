"""Formatting functions.

Contains wrapper patterns, partial application, and specialization demos.
"""


# ── Wrapper pattern (both wrap the same target) ──────────────────

def format_as_json(data: dict, indent: int = 2) -> str:
    import json
    return json.dumps(data, indent=indent)


def format_as_compact_json(data: dict) -> str:
    import json
    return json.dumps(data, separators=(",", ":"))


# ── Wrapper pattern (different targets) ──────────────────────────

def write_yaml(data: dict, path: str) -> None:
    import yaml
    yaml.dump(data, open(path, "w"))


def write_toml(data: dict, path: str) -> None:
    import tomli_w
    tomli_w.dump(data, open(path, "wb"))


# ── Currying / partial application ───────────────────────────────

def make_prefix_formatter(prefix: str):
    return lambda text: f"{prefix}: {text}"


def make_suffix_formatter(suffix: str):
    return lambda text: f"{text} ({suffix})"


def make_bracketed_formatter(left: str, right: str):
    return lambda text: f"{left}{text}{right}"


# ── Near-match: same structure, different literals ───────────────

def format_error_message(code: int, detail: str) -> str:
    header = "ERROR"
    separator = "---"
    lines = [header, separator, f"Code: {code}", f"Detail: {detail}", separator]
    return "\n".join(lines)


def format_warning_message(code: int, detail: str) -> str:
    header = "WARNING"
    separator = "==="
    lines = [header, separator, f"Code: {code}", f"Detail: {detail}", separator]
    return "\n".join(lines)


# ── Exact duplicate across files (matches data_utils) ────────────

def clean_record(record: dict) -> dict:
    cleaned = {}
    for key, value in record.items():
        key = key.strip().lower()
        value = str(value).strip()
        if not value:
            continue
        cleaned[key] = value
    return cleaned
