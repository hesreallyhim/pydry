from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar


@dataclass
class PluginContext:
    occurrence: Any
    node: Any
    features: dict[str, Any]


@dataclass
class PairContext:
    a: PluginContext
    b: PluginContext
    evidence: Any


@dataclass
class PairPluginResult:
    pattern_labels: list[str] = field(default_factory=list)
    key_differences: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    suggested_refactor_kind: str | None = None
    refactorability_delta: float = 0.0
    abstract_template: str | None = None


class PairPlugin(Protocol):
    name: str

    def analyze_pair(self, ctx: PairContext) -> PairPluginResult: ...


class PluginRegistry:
    def __init__(self) -> None:
        self._pair_plugins: list[PairPlugin] = []

    def register_pair(self, plugin: PairPlugin) -> None:
        self._pair_plugins.append(plugin)

    @property
    def pair_plugins(self) -> list[PairPlugin]:
        return list(self._pair_plugins)


registry = PluginRegistry()


_T = TypeVar("_T")


def register_pair_plugin(plugin: _T) -> _T:
    obj: PairPlugin = plugin() if isinstance(plugin, type) else plugin  # type: ignore[assignment]
    registry.register_pair(obj)
    return plugin


def _uniq(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def apply_pair_plugins(
    ctx: PairContext, *, plugin_errors: list[str] | None = None
) -> PairPluginResult:
    merged = PairPluginResult()
    pair_errors: list[str] = []
    for plugin in registry.pair_plugins:
        try:
            result = plugin.analyze_pair(ctx)
        except Exception as exc:
            msg = f"{plugin.name}: {type(exc).__name__}: {exc}"
            pair_errors.append(msg)
            if plugin_errors is not None:
                plugin_errors.append(msg)
            continue
        if result is None:
            continue
        merged.pattern_labels.extend(result.pattern_labels)
        merged.key_differences.extend(result.key_differences)
        merged.risk_flags.extend(result.risk_flags)
        merged.metadata[plugin.name] = result.metadata
        merged.refactorability_delta += result.refactorability_delta
        if (
            result.suggested_refactor_kind is not None
            and merged.suggested_refactor_kind is None
        ):
            merged.suggested_refactor_kind = result.suggested_refactor_kind
        if result.abstract_template is not None and merged.abstract_template is None:
            merged.abstract_template = result.abstract_template
    merged.pattern_labels = _uniq(merged.pattern_labels)
    merged.key_differences = _uniq(merged.key_differences)
    merged.risk_flags = _uniq(merged.risk_flags)
    if pair_errors:
        merged.metadata["_plugin_errors"] = pair_errors
    return merged
