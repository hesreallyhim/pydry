"""Pipelines with a repeated block and a specialized sibling.

Exercises block clone detection (a parsing block copied into a larger
function) and loose alignment (a function that hard-codes a parameter of
its more general sibling).
"""


# ── Repeated block: the parsing loop is copied into a larger pipeline ──

def parse_records(path: str) -> list[dict]:
    with open(path) as fh:
        raw = fh.read()
    rows = raw.splitlines()
    records = []
    for row in rows:
        if not row.strip():
            continue
        parts = row.split(",")
        record = {"id": int(parts[0]), "name": parts[1].strip()}
        records.append(record)
    records.sort(key=lambda r: r["id"])
    return records


def summarize_file(path: str) -> dict:
    with open(path) as fh:
        raw = fh.read()
    rows = raw.splitlines()
    records = []
    for row in rows:
        if not row.strip():
            continue
        parts = row.split(",")
        record = {"id": int(parts[0]), "name": parts[1].strip()}
        records.append(record)
    records.sort(key=lambda r: r["id"])
    lengths = {}
    for record in records:
        lengths[record["id"]] = len(record["name"])
    histogram = {}
    for length in lengths.values():
        histogram[length] = histogram.get(length, 0) + 1
    mode = None
    for length, count in histogram.items():
        if mode is None or count > mode[1]:
            mode = (length, count)
    summary = {"count": len(records), "mode": mode}
    if summary["count"] == 0:
        summary["empty"] = True
    return summary


# ── Parameter specialization: a sibling with one argument hard-coded ──

def retry(operation, attempts: int, delay: float):
    last_error = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            sleep(delay)
    raise RuntimeError(f"failed after {attempts} attempts") from last_error


def retry_quickly(operation, attempts: int):
    last_error = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            sleep(0.1)
    raise RuntimeError(f"failed after {attempts} attempts") from last_error


def sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
