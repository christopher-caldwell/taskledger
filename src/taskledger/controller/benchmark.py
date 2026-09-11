"""Versioned, deterministic benchmark normalization and comparison.

This module deliberately has no provider, database, or model dependency.  It
compares supplied measurements and reports qualifications; it never invents a
valuation or declares a winner from incomparable evidence.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


SCHEMA_VERSION = "taskledger-benchmark-measurement-v1"
OUTCOMES = {"COMPLETED_VERIFIED", "INCOMPLETE", "FAILED", "UNKNOWN"}
ACCOUNTING = {"COMPLETE", "ESTIMATE", "PARTIAL_MISSING", "LEGACY_METHOD"}
COMPARABILITY = {"MATCHED", "QUALIFIED_DESCRIPTIVE", "INSUFFICIENT_EVIDENCE"}
TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_tokens")
MATCH_FIELDS = ("workload_identity", "starting_code_identity", "review_standard", "measurement_scope", "role_configuration_identity")


def normalize_measurement(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a stable normalized measurement."""
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    result = deepcopy(value)
    if result.get("outcome") not in OUTCOMES:
        raise ValueError("invalid benchmark outcome")
    if result.get("accounting") not in ACCOUNTING:
        raise ValueError("invalid accounting classification")
    if not isinstance(result.get("provenance"), dict) or not result["provenance"].get("source"):
        raise ValueError("measurement provenance.source is required")
    usage = result.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("usage is required")
    for field in TOKEN_FIELDS:
        number = usage.get(field)
        if number is not None and (not isinstance(number, int) or isinstance(number, bool) or number < 0):
            raise ValueError(f"{field} must be a non-negative integer or unavailable")
    if usage.get("input_tokens") is not None and usage.get("cached_input_tokens") is not None and usage["cached_input_tokens"] > usage["input_tokens"]:
        raise ValueError("cached input cannot exceed inclusive input")
    result["usage"] = {field: usage.get(field) for field in TOKEN_FIELDS}
    result["usage"]["admission_tokens"] = (
        usage["input_tokens"] + usage["output_tokens"]
        if usage.get("input_tokens") is not None and usage.get("output_tokens") is not None else None
    )
    result["usage"]["non_cached_input_tokens"] = (
        usage["input_tokens"] - usage["cached_input_tokens"]
        if usage.get("input_tokens") is not None and usage.get("cached_input_tokens") is not None else None
    )
    result.setdefault("valuation", None)
    return result


def compare_measurements(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Compare two normalized measurements without unsupported causal claims."""
    a, b = normalize_measurement(left), normalize_measurement(right)
    mismatches = [field for field in MATCH_FIELDS if a.get(field) != b.get(field) or a.get(field) is None]
    if not mismatches and a["accounting"] == b["accounting"] == "COMPLETE":
        comparability = "MATCHED"
    elif all(a.get(field) is not None and b.get(field) is not None for field in MATCH_FIELDS):
        comparability = "QUALIFIED_DESCRIPTIVE"
    else:
        comparability = "INSUFFICIENT_EVIDENCE"
    deltas = {}
    for field in (*TOKEN_FIELDS, "admission_tokens", "non_cached_input_tokens"):
        av, bv = a["usage"].get(field), b["usage"].get(field)
        deltas[field] = bv - av if av is not None and bv is not None else None
    warnings = [f"mismatch: {field}" for field in mismatches]
    if a["outcome"] != b["outcome"]:
        warnings.append("outcomes differ; cost alone cannot select a winner")
    if a["accounting"] != "COMPLETE" or b["accounting"] != "COMPLETE":
        warnings.append("at least one accounting method is incomplete, estimated, or legacy")
    if a.get("valuation") != b.get("valuation") or a.get("valuation") is None:
        warnings.append("no common immutable valuation snapshot; monetary comparison unavailable")
    return {
        "schema_version": "taskledger-benchmark-comparison-v1",
        "left": {"outcome": a["outcome"], "accounting": a["accounting"], "source": a["provenance"]["source"]},
        "right": {"outcome": b["outcome"], "accounting": b["accounting"], "source": b["provenance"]["source"]},
        "comparability": comparability,
        "mismatches": mismatches,
        "usage_delta_right_minus_left": deltas,
        "valuation_comparison": "UNAVAILABLE" if warnings and warnings[-1].startswith("no common") else "SUPPLIED_SNAPSHOT",
        "winner": None,
        "warnings": warnings,
    }
