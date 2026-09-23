"""Built-in pair plugins.

The engine derives pattern labels from statement alignment. These plugins add
the risk flags that should discourage a merge. Each risk carries its own
refactorability penalty, so the engine applies none of its own for the same
flags.
"""

from __future__ import annotations

from .plugins import PairContext, PairPluginResult, register_pair_plugin


@register_pair_plugin
class SideEffectRiskPlugin:
    name = "side_effects"

    def analyze_pair(self, ctx: PairContext) -> PairPluginResult:
        a = ctx.a.features
        b = ctx.b.features
        calls = sorted(
            set(a.get("side_effect_calls", [])) | set(b.get("side_effect_calls", []))
        )
        if not calls:
            return PairPluginResult()
        return PairPluginResult(
            risk_flags=["possible_side_effects"],
            refactorability_delta=-0.05,
            metadata={"calls": calls},
        )


@register_pair_plugin
class AsyncBoundaryPlugin:
    name = "async_boundary"

    def analyze_pair(self, ctx: PairContext) -> PairPluginResult:
        a = ctx.a.features
        b = ctx.b.features
        flags = []
        diffs = []
        delta = 0.0
        if a.get("has_await") != b.get("has_await"):
            flags.append("async_boundary_diff")
            diffs.append("async behavior differs")
            delta -= 0.15
        if a.get("has_yield") != b.get("has_yield"):
            flags.append("return_shape_diff")
            diffs.append("generator behavior differs")
            delta -= 0.15
        if a.get("raises") != b.get("raises"):
            flags.append("exception_behavior_diff")
            diffs.append("exception behavior differs")
            delta -= 0.05
        if not flags:
            return PairPluginResult()
        return PairPluginResult(
            risk_flags=flags,
            key_differences=diffs,
            refactorability_delta=delta,
            metadata={
                "a_has_await": a.get("has_await"),
                "b_has_await": b.get("has_await"),
                "a_has_yield": a.get("has_yield"),
                "b_has_yield": b.get("has_yield"),
                "a_raises": a.get("raises"),
                "b_raises": b.get("raises"),
            },
        )


@register_pair_plugin
class DependencyDivergencePlugin:
    name = "dependency_divergence"

    threshold = 4

    def analyze_pair(self, ctx: PairContext) -> PairPluginResult:
        a_names = set(ctx.a.features.get("external_names", ()))
        b_names = set(ctx.b.features.get("external_names", ()))
        divergent = sorted(a_names ^ b_names)
        if len(divergent) < self.threshold:
            return PairPluginResult()
        return PairPluginResult(
            risk_flags=["ambient_dependency_diff"],
            key_differences=[
                f"{len(divergent)} module-level name(s) used by only one side"
            ],
            refactorability_delta=-0.06,
            metadata={"divergent_names": divergent[:12]},
        )
