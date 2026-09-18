"""Post-build exception detection — flags rows already written into NEO.csv/LAO.csv
that are still missing critical data, or hold gibberish in a critical field.

This is a different, later check than core/normalizer.py's pre-build quarantine:
normalizer decides whether a SOURCE row is admissible at all (raw customer-file
columns, before the canonical NEO/LAO schema exists) — a row it rejects never becomes a
NEO/LAO row in the first place, and its "Normalized.csv"/"Exceptions.csv" output is a
single combined file across both targets. This module runs strictly AFTER
builders.build_target() and cross_reference.enrich() — i.e. against the final canonical
row a customer actually receives — and produces one companion file PER TARGET
(NEO_Exceptions.csv / LAO_Exceptions.csv). A row can clear normalizer's checks (it has a
real join key/identity) and still land here, e.g. its AssetName or ComponentCode never
resolved to any source column and no cross-reference join recovered it either.

Flagged rows are a read-only audit, not a quarantine: they are NOT removed from
NEO.csv/LAO.csv — the exception file is a companion view for review, output alongside
the main CSV, same as the project's existing "never invent, always auditable" pattern
elsewhere (see core/cross_reference.py).

`critical_fields` deliberately does not mean "every blank cell in the row" — many
fields are legitimately blank by this project's own design (e.g. FrequencyValue for
Rio Tinto's LAO, a documented real gap in that customer's data, not a mapping miss).
Flagging every such row would bury the genuinely actionable exceptions in noise. The
default critical set (AssetName, SerialNumber, ComponentCode, ModifierCode) is exactly
the identity + AMT-key fields this project already treats as load-bearing — the same
fields the downstream Snowflake key is built from (see core/cross_reference.py) — so a
blank there means the row can't be keyed downstream, not just that one column is thin.
"""
from __future__ import annotations
import pandas as pd

_GIBBERISH_PATTERN = r"^[^A-Za-z0-9]{1,2}$"
_BLANK_TOKENS = ("", "nan", "none", "null")


def _is_blank(series: pd.Series) -> pd.Series:
    # .str.* accessor methods are NaN-safe (propagate NaN rather than raising), unlike
    # a raw re.match() applied via .map() — see _is_gibberish's history for why that
    # distinction matters: .astype(str) does not reliably turn every missing value into
    # the literal string "nan" across pandas versions/dtypes.
    return series.isna() | series.astype(str).str.strip().str.lower().isin(_BLANK_TOKENS)


def _is_gibberish(series: pd.Series) -> pd.Series:
    # Vectorized .str.match (NaN-safe) instead of series.map(lambda v: _GIBBERISH.match(v)):
    # the latter passes raw, non-string values (e.g. a leftover float NaN that
    # .astype(str) didn't stringify) straight to re.match() and crashes with
    # "expected string or bytes-like object, got 'float'".
    stripped = series.astype(str).str.strip()
    return stripped.str.match(_GIBBERISH_PATTERN).fillna(False)


def find_exceptions(out: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """Return the subset of `out` (same columns, plus `_exception_reason`) that trips
    any configured rule. `out` itself is never modified — callers still write the full,
    untouched frame to NEO.csv/LAO.csv; this is a separate, additional view.

    `rules` (from customer config, `cfg["exceptions"]`):
      - "critical_fields": columns that must not be blank.
      - "gibberish_fields": columns checked for a single/double junk-character value
        (same pattern normalizer.py uses for source-row gibberish).
    Either list may name a column this target's output_columns doesn't actually have
    (e.g. a future target); such names are silently skipped, not an error, so one shared
    rule set can be reused across targets with different schemas.
    """
    reasons = pd.Series([None] * len(out), index=out.index, dtype="object")

    def _flag(mask: pd.Series, reason: str) -> None:
        nonlocal reasons
        reasons = reasons.mask(mask & reasons.isna(), reason)

    for field in rules.get("critical_fields", []):
        if field in out.columns:
            _flag(_is_blank(out[field]), f"missing {field}")

    for field in rules.get("gibberish_fields", []):
        if field in out.columns:
            _flag(_is_gibberish(out[field]), f"gibberish {field}")

    flagged = reasons.notna()
    exceptions = out[flagged].copy()
    exceptions["_exception_reason"] = reasons[flagged]
    return exceptions
