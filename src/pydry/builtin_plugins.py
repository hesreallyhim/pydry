from __future__ import annotations

from typing import Any

from .plugins import PairContext, PairPluginResult, register_pair_plugin


def _literal_token_diff(a: dict[str, Any], b: dict[str, Any]) -> int:
    a_tokens: dict[str, int] = a.get("literal_tokens", {})
    b_tokens: dict[str, int] = b.get("literal_tokens", {})
    keys = set(a_tokens) | set(b_tokens)
    return sum(abs(a_tokens.get(k, 0) - b_tokens.get(k, 0)) for k in keys)


@register_pair_plugin
class WrapperPlugin:
    name = "wrapper"

    def analyze_pair(self, ctx: PairContext) -> PairPluginResult:
        a = ctx.a.features
        b = ctx.b.features
        evidence = ctx.evidence
        if evidence.wrapper_score < 0.5:
            return PairPluginResult()
        metadata = {
            "a_wrapper_target": a.get("wrapper_target"),
            "b_wrapper_target": b.get("wrapper_target"),
            "a_fixed_args": a.get("fixed_args", 0),
            "b_fixed_args": b.get("fixed_args", 0),
        }
        return PairPluginResult(
            pattern_labels=["wrapper"],
            key_differences=(
                ["wrapper targets differ"]
                if a.get("wrapper_target") != b.get("wrapper_target")
                else []
            ),
            suggested_refactor_kind="merge_into_single_function_with_param",
            refactorability_delta=0.05,
            metadata=metadata,
        )


@register_pair_plugin
class CurryingPlugin:
    name = "currying"

    def analyze_pair(self, ctx: PairContext) -> PairPluginResult:
        a = ctx.a.features
        b = ctx.b.features
        evidence = ctx.evidence
        if evidence.curry_score < 0.4:
            return PairPluginResult()
        return PairPluginResult(
            pattern_labels=["partial_application"],
            suggested_refactor_kind="introduce_partial",
            refactorability_delta=0.04,
            metadata={
                "a_returns_lambda": a.get("returns_lambda"),
                "b_returns_lambda": b.get("returns_lambda"),
                "a_curry_depth": a.get("curry_depth"),
                "b_curry_depth": b.get("curry_depth"),
            },
        )


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
            delta -= 0.08
        if a.get("has_yield") != b.get("has_yield"):
            flags.append("return_shape_diff")
            diffs.append("generator behavior differs")
            delta -= 0.08
        if a.get("raises") != b.get("raises"):
            flags.append("exception_behavior_diff")
            diffs.append("exception behavior differs")
            delta -= 0.05
        if not flags and not diffs:
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
class LiteralSpecializationPlugin:
    name = "literal_specialization"

    def analyze_pair(self, ctx: PairContext) -> PairPluginResult:
        a = ctx.a.features
        b = ctx.b.features
        e = ctx.evidence
        literal_token_diff = _literal_token_diff(a, b)
        if (
            literal_token_diff > 0
            and abs(a.get("literals", 0) - b.get("literals", 0)) <= 2
            and e.shape_similarity >= 0.85
            and e.call_similarity >= 0.6
        ):
            return PairPluginResult(
                pattern_labels=["literal_specialization"],
                suggested_refactor_kind="parameterize_constant",
                refactorability_delta=0.03,
                abstract_template=(
                    "def shared_helper(..., configurable_value):\n"
                    "    # parameterize constant-like variation\n"
                    "    ..."
                ),
                metadata={
                    "a_literals": a.get("literals"),
                    "b_literals": b.get("literals"),
                    "literal_token_diff": literal_token_diff,
                },
            )
        return PairPluginResult()


@register_pair_plugin
class ExtractHelperPlugin:
    name = "extract_helper"

    def analyze_pair(self, ctx: PairContext) -> PairPluginResult:
        e = ctx.evidence
        if e.shape_similarity >= 0.8 and e.stmt_similarity >= 0.8:
            return PairPluginResult(
                pattern_labels=["extract_helper_candidate"],
                suggested_refactor_kind="extract_common_helper",
                refactorability_delta=0.05,
                abstract_template=(
                    "def shared_helper(...):\n"
                    "    # candidate abstraction for "
                    f"{ctx.a.occurrence.qualname}"
                    f" and {ctx.b.occurrence.qualname}\n"
                    "    ..."
                ),
                metadata={
                    "shape_similarity": e.shape_similarity,
                    "stmt_similarity": e.stmt_similarity,
                },
            )
        return PairPluginResult()


@register_pair_plugin
class DependencyDivergencePlugin:
    name = "dependency_divergence"

    def analyze_pair(self, ctx: PairContext) -> PairPluginResult:
        a = ctx.a.features
        b = ctx.b.features
        e = ctx.evidence
        ext_diff = len(
            set(a.get("external_names", {})) ^ set(b.get("external_names", {}))
        )
        if ext_diff >= 6:
            return PairPluginResult(
                risk_flags=["ambient_dependency_diff"],
                pattern_labels=(
                    ["same_shape_different_dependencies"]
                    if e.signature_similarity >= 0.8 and e.call_similarity < 0.5
                    else []
                ),
                refactorability_delta=-0.06,
                metadata={"external_name_symmetric_difference": ext_diff},
            )
        return PairPluginResult()
