"""Async and sync handler pairs.

Triggers async_boundary, return_shape_diff, and side-effect detection.
"""


# ── Async boundary: sync vs async pair ───────────────────────────

def fetch_user(user_id: int) -> dict:
    import requests
    resp = requests.get(f"https://api.example.com/users/{user_id}")
    data = resp.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data


async def fetch_user_async(user_id: int) -> dict:
    import aiohttp
    async with aiohttp.ClientSession() as session:
        resp = await session.get(f"https://api.example.com/users/{user_id}")
        data = await resp.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data


# ── Generator vs list builder (return shape diff) ────────────────

def collect_even(numbers: list[int]) -> list[int]:
    result = []
    for n in numbers:
        if n % 2 == 0:
            result.append(n)
    return result


def iter_even(numbers: list[int]):
    for n in numbers:
        if n % 2 == 0:
            yield n


# ── Side-effect pair (both write, slight structural diff) ────────

def save_report_txt(report: dict, path: str) -> None:
    lines = []
    for key, value in report.items():
        lines.append(f"{key}: {value}")
    content = "\n".join(lines)
    with open(path, "w") as f:
        f.write(content)


def save_report_csv(report: dict, path: str) -> None:
    lines = [",".join(report.keys())]
    lines.append(",".join(str(v) for v in report.values()))
    content = "\n".join(lines)
    with open(path, "w") as f:
        f.write(content)


# ── Nested class methods (exact duplicate at method level) ───────

class UserSerializer:
    def to_dict(self, obj: dict) -> dict:
        result = {}
        for key, value in obj.items():
            if value is not None:
                result[key] = str(value)
        return result


class OrderSerializer:
    def to_dict(self, obj: dict) -> dict:
        output = {}
        for key, value in obj.items():
            if value is not None:
                output[key] = str(value)
        return output
