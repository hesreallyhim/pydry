from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, cast


class ConfigError(ValueError):
    """Raised when pydry configuration cannot be loaded or validated."""


@dataclass(frozen=True)
class CheckConfig:
    """Effective settings for the policy-oriented check command."""

    root: str = "."
    threshold: float = 0.8
    top_k: int = 200
    top_level_only: bool = False
    strict: bool = True
    normalize_local_names: bool = True
    normalize_constants: bool = True
    max_exact_groups: int | None = 0
    max_near_matches: int | None = None
    max_abstract_candidates: int | None = 0
    fail_on_scan_errors: bool = True
    fail_on_plugin_errors: bool = True
    annotation_limit: int = 10


_CONFIG_KEYS = {field.name for field in fields(CheckConfig)}
_BOOL_KEYS = {
    "top_level_only",
    "strict",
    "normalize_local_names",
    "normalize_constants",
    "fail_on_scan_errors",
    "fail_on_plugin_errors",
}
_OPTIONAL_COUNT_KEYS = {
    "max_exact_groups",
    "max_near_matches",
    "max_abstract_candidates",
}


def _read_pydry_table(path: Path) -> dict[str, object]:
    try:
        parsed: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Could not read configuration {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc

    tool = parsed.get("tool", {})
    if not isinstance(tool, dict):
        raise ConfigError(f"[tool] in {path} must be a table")
    raw = tool.get("pydry", {})
    if not isinstance(raw, dict):
        raise ConfigError(f"[tool.pydry] in {path} must be a table")
    return {str(key): value for key, value in raw.items()}


def _validate_count(key: str, value: object, *, optional: bool) -> int | None:
    if optional and value is None:
        return None
    if optional and value == "none":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"tool.pydry.{key} must be an integer")
    if value < 0:
        raise ConfigError(f"tool.pydry.{key} must be >= 0")
    return value


def _validate_config_values(values: dict[str, object]) -> dict[str, object]:
    unknown = sorted(set(values) - _CONFIG_KEYS)
    if unknown:
        joined = ", ".join(unknown)
        raise ConfigError(f"Unknown [tool.pydry] setting(s): {joined}")

    validated: dict[str, object] = {}
    for key, value in values.items():
        if key == "root":
            if not isinstance(value, str) or not value:
                raise ConfigError("tool.pydry.root must be a non-empty string")
            validated[key] = value
        elif key == "threshold":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError("tool.pydry.threshold must be a number")
            threshold = float(value)
            if not 0.0 <= threshold <= 1.0:
                raise ConfigError("tool.pydry.threshold must be between 0 and 1")
            validated[key] = threshold
        elif key == "top_k":
            count = _validate_count(key, value, optional=False)
            assert count is not None
            validated[key] = count
        elif key in _OPTIONAL_COUNT_KEYS:
            validated[key] = _validate_count(key, value, optional=True)
        elif key == "annotation_limit":
            count = _validate_count(key, value, optional=False)
            assert count is not None
            validated[key] = count
        elif key in _BOOL_KEYS:
            if not isinstance(value, bool):
                raise ConfigError(f"tool.pydry.{key} must be true or false")
            validated[key] = value
    return validated


def load_check_config(config_path: Path | None) -> CheckConfig:
    """Load `[tool.pydry]`, discovering pyproject.toml when no path is given."""

    path = config_path
    if path is None:
        discovered = Path("pyproject.toml")
        if not discovered.is_file():
            return CheckConfig()
        path = discovered
    elif not path.is_file():
        raise ConfigError(f"Configuration file does not exist: {path}")

    values = _validate_config_values(_read_pydry_table(path))
    return _merge(CheckConfig(), values)


def apply_overrides(config: CheckConfig, **overrides: object) -> CheckConfig:
    """Apply non-None CLI or action values over repository configuration."""

    supplied = {key: value for key, value in overrides.items() if value is not None}
    for key in _OPTIONAL_COUNT_KEYS:
        if supplied.get(key) == "none":
            supplied[key] = None
    return _merge(config, supplied)


def _merge(config: CheckConfig, values: dict[str, object]) -> CheckConfig:
    return CheckConfig(
        root=cast("str", values.get("root", config.root)),
        threshold=cast("float", values.get("threshold", config.threshold)),
        top_k=cast("int", values.get("top_k", config.top_k)),
        top_level_only=cast(
            "bool", values.get("top_level_only", config.top_level_only)
        ),
        strict=cast("bool", values.get("strict", config.strict)),
        normalize_local_names=cast(
            "bool",
            values.get("normalize_local_names", config.normalize_local_names),
        ),
        normalize_constants=cast(
            "bool", values.get("normalize_constants", config.normalize_constants)
        ),
        max_exact_groups=cast(
            "int | None", values.get("max_exact_groups", config.max_exact_groups)
        ),
        max_near_matches=cast(
            "int | None", values.get("max_near_matches", config.max_near_matches)
        ),
        max_abstract_candidates=cast(
            "int | None",
            values.get("max_abstract_candidates", config.max_abstract_candidates),
        ),
        fail_on_scan_errors=cast(
            "bool", values.get("fail_on_scan_errors", config.fail_on_scan_errors)
        ),
        fail_on_plugin_errors=cast(
            "bool",
            values.get("fail_on_plugin_errors", config.fail_on_plugin_errors),
        ),
        annotation_limit=cast(
            "int", values.get("annotation_limit", config.annotation_limit)
        ),
    )
