"""Validation functions.

Contains near-matches with control flow, exception behavior, and
different dependency profiles.
"""


# ── Near-match: same shape, different dependencies ───────────────

def validate_email(value: str) -> bool:
    import re
    pattern = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")
    if not isinstance(value, str):
        raise TypeError("expected string")
    value = value.strip().lower()
    if not value:
        return False
    if not pattern.match(value):
        return False
    return True


def validate_url(value: str) -> bool:
    from urllib.parse import urlparse
    parsed = urlparse(value)
    if not isinstance(value, str):
        raise TypeError("expected string")
    value = value.strip().lower()
    if not value:
        return False
    if not parsed.scheme or not parsed.netloc:
        return False
    return True


# ── Near-match: exception behavior differs ───────────────────────

def parse_int_strict(text: str) -> int:
    text = text.strip()
    if not text:
        raise ValueError("empty input")
    if not text.lstrip("-").isdigit():
        raise ValueError(f"not a number: {text!r}")
    return int(text)


def parse_int_lenient(text: str) -> int:
    text = text.strip()
    if not text:
        return 0
    if not text.lstrip("-").isdigit():
        return 0
    return int(text)


# ── Near-match with control flow complexity difference ───────────

def filter_active_users(users: list[dict]) -> list[dict]:
    result = []
    for user in users:
        if user.get("active"):
            result.append(user)
    return result


def filter_active_admins(users: list[dict]) -> list[dict]:
    result = []
    for user in users:
        if user.get("active"):
            if user.get("role") == "admin":
                if not user.get("suspended"):
                    result.append(user)
    return result


# ── Near-match: high similarity with different param counts ──────

def clamp_value(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def clamp_to_unit(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
