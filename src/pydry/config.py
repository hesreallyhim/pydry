from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when pydry configuration cannot be loaded or validated."""


CONFIG_FILENAME = "pydry.toml"


@dataclass(frozen=True)
class CheckConfig:
    """Effective settings for the policy-oriented check command."""

    root: str = "."
    profile: str = "balanced"
    threshold: float = 0.8
    top_k: int = 200
    top_level_only: bool = False
    strict: bool = True
    normalize_local_names: bool = True
    normalize_constants: bool = True
    min_statements: int = 2
    ignore_trivial: bool = True
    block_min_statements: int = 6
    exclude: tuple[str, ...] = ()
    baseline: str | None = None
    max_exact_groups: int | None = 0
    max_near_matches: int | None = None
    max_abstract_candidates: int | None = None
    max_block_clones: int | None = 0
    fail_on_scan_errors: bool = True
    fail_on_plugin_errors: bool = True
    annotation_limit: int = 10


PROFILES: dict[str, dict[str, object]] = {
    "strict": {
        "threshold": 0.8,
        "min_statements": 2,
        "block_min_statements": 5,
        "max_exact_groups": 0,
        "max_abstract_candidates": 0,
        "max_block_clones": 0,
    },
    "balanced": {},
    "lenient": {
        "threshold": 0.85,
        "min_statements": 4,
        "block_min_statements": 8,
        "max_exact_groups": 0,
        "max_block_clones": None,
    },
}

_CONFIG_KEYS = {field.name for field in fields(CheckConfig)}
_BOOL_KEYS = {
    "top_level_only",
    "strict",
    "normalize_local_names",
    "normalize_constants",
    "ignore_trivial",
    "fail_on_scan_errors",
    "fail_on_plugin_errors",
}
_COUNT_KEYS = {"top_k", "annotation_limit", "min_statements", "block_min_statements"}
_OPTIONAL_COUNT_KEYS = {
    "max_exact_groups",
    "max_near_matches",
    "max_abstract_candidates",
    "max_block_clones",
}


def _read_config(path: Path) -> dict[str, object]:
    try:
        parsed: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Could not read configuration {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc

    return {str(key): value for key, value in parsed.items()}


def _validate_count(key: str, value: object, *, optional: bool) -> int | None:
    if optional and value is None:
        return None
    if optional and value == "none":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        expected = "an integer or the string 'none'" if optional else "an integer"
        raise ConfigError(f"{key} must be {expected}")
    if value < 0:
        raise ConfigError(f"{key} must be >= 0")
    return value


def _validate_config_values(values: dict[str, object]) -> dict[str, object]:
    unknown = sorted(set(values) - _CONFIG_KEYS)
    if unknown:
        joined = ", ".join(unknown)
        raise ConfigError(f"Unknown pydry setting(s): {joined}")

    validated: dict[str, object] = {}
    for key, value in values.items():
        if key == "root":
            if not isinstance(value, str) or not value:
                raise ConfigError("root must be a non-empty string")
            validated[key] = value
        elif key == "profile":
            if not isinstance(value, str) or value not in PROFILES:
                names = ", ".join(sorted(PROFILES))
                raise ConfigError(f"profile must be one of: {names}")
            validated[key] = value
        elif key == "baseline":
            if value is not None and (not isinstance(value, str) or not value):
                raise ConfigError("baseline must be a non-empty string")
            validated[key] = value
        elif key == "exclude":
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value
            ):
                raise ConfigError("exclude must be a list of non-empty strings")
            validated[key] = tuple(value)
        elif key == "threshold":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError("threshold must be a number")
            threshold = float(value)
            if not 0.0 <= threshold <= 1.0:
                raise ConfigError("threshold must be between 0 and 1")
            validated[key] = threshold
        elif key in _COUNT_KEYS:
            count = _validate_count(key, value, optional=False)
            assert count is not None
            if key == "block_min_statements" and count < 2:
                raise ConfigError("block_min_statements must be >= 2")
            validated[key] = count
        elif key in _OPTIONAL_COUNT_KEYS:
            validated[key] = _validate_count(key, value, optional=True)
        elif key in _BOOL_KEYS:
            if not isinstance(value, bool):
                raise ConfigError(f"{key} must be true or false")
            validated[key] = value
    return validated


def load_check_config(config_path: Path | None) -> CheckConfig:
    """Load a standalone TOML policy, discovering pydry.toml by default."""

    path = config_path
    if path is None:
        discovered = Path(CONFIG_FILENAME)
        if not discovered.is_file():
            return CheckConfig()
        path = discovered
    elif not path.is_file():
        raise ConfigError(f"Configuration file does not exist: {path}")
    if path.name != CONFIG_FILENAME:
        raise ConfigError(f"Configuration file must be named {CONFIG_FILENAME}: {path}")

    values = _validate_config_values(_read_config(path))
    return _merge(CheckConfig(), values)


def apply_overrides(config: CheckConfig, **overrides: object) -> CheckConfig:
    """Apply non-None CLI or action values over repository configuration."""

    supplied = {key: value for key, value in overrides.items() if value is not None}
    for key in _OPTIONAL_COUNT_KEYS:
        if supplied.get(key) == "none":
            supplied[key] = None
    if "exclude" in supplied:
        supplied["exclude"] = tuple(supplied["exclude"])  # type: ignore[arg-type]
    return _merge(config, supplied)


def _merge(config: CheckConfig, values: dict[str, object]) -> CheckConfig:
    """Apply a profile's defaults first, then the explicitly supplied keys."""

    profile = values.get("profile", config.profile)
    assert isinstance(profile, str)
    merged = (
        replace(config, **PROFILES[profile])  # type: ignore[arg-type]
        if "profile" in values
        else config
    )
    return replace(merged, **values)  # type: ignore[arg-type]
