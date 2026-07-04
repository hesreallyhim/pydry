"""Data processing utilities.

Contains exact duplicates and near-matches for pydry demo.
"""


# ── Exact duplicates (identical after AST normalization) ─────────

def sum_positive(values: list[int]) -> int:
    """Return sum of positive values."""
    total = 0
    for v in values:
        if v > 0:
            total += v
    return total


def add_positive_numbers(numbers: list[int]) -> int:
    """Add up only the positive numbers."""
    result = 0
    for n in numbers:
        if n > 0:
            result += n
    return result


# ── Exact duplicates with constant normalization ─────────────────

def paginate_results(items: list, page: int) -> list:
    start = page * 20
    end = start + 20
    return items[start:end]


def paginate_logs(entries: list, page: int) -> list:
    start = page * 50
    end = start + 50
    return entries[start:end]


# ── Near-match: literal specialization ───────────────────────────

def build_user_query(name: str, active: bool) -> dict:
    query = {"type": "user", "version": 2}
    if active:
        query["status"] = "active"
    query["name"] = name
    return query


def build_admin_query(name: str, active: bool) -> dict:
    query = {"type": "admin", "version": 3}
    if active:
        query["status"] = "active"
    query["name"] = name
    return query


# ── Near-match: extract helper candidate ─────────────────────────

def process_csv_row(row: dict) -> dict:
    cleaned = {}
    for key, value in row.items():
        key = key.strip().lower()
        value = str(value).strip()
        if not value:
            continue
        cleaned[key] = value
    return cleaned


def process_json_entry(entry: dict) -> dict:
    normalized = {}
    for key, value in entry.items():
        key = key.strip().lower()
        value = str(value).strip()
        if not value:
            continue
        normalized[key] = value
    return normalized


# ── Near-match: renamed locals (high shape + call similarity) ────

def load_and_transform(path: str) -> list[dict]:
    import json
    with open(path) as fh:
        raw = json.load(fh)
    output = []
    for item in raw:
        transformed = {k.lower(): v for k, v in item.items()}
        output.append(transformed)
    return output


def read_and_convert(filepath: str) -> list[dict]:
    import json
    with open(filepath) as f:
        data = json.load(f)
    result = []
    for record in data:
        converted = {k.lower(): v for k, v in record.items()}
        result.append(converted)
    return result
