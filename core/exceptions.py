"""Post-build exception detection — NEO_Exceptions.csv/LAO_Exceptions.csv, covering two
different severities of the same underlying idea ("this row can't be trusted/keyed the
way it stands"), both driven by this module:

  - `find_exceptions()`: an AUDIT. A row that's still missing a critical field, or holds
    gibberish in one, is flagged with `_exception_reason` but left in NEO.csv/LAO.csv
    exactly as built — a companion review view, not a quarantine.
  - `split_blank_pair()`: a REMOVAL. A row whose ComponentCode AND ModifierCode are
    BOTH blank — the AMT-key pair together, not either one alone — carries no usable
    component identity at all, so it's actually taken out of NEO.csv/LAO.csv and
    diverted here instead. Every row this function returns gets
    `_removed_from_output=True`; every row `find_exceptions()` flags gets `False` — the
    two are combined into one file by mapping_engine.py, and that column is how a
    reader tells "still shipped, please review" from "removed, no longer in NEO/LAO"
    without needing to parse the reason text.

This is a different, later check than core/normalizer.py's pre-build quarantine:
normalizer decides whether a SOURCE row is admissible at all (raw customer-file
columns, before the canonical NEO/LAO schema exists) — a row it rejects never becomes a
NEO/LAO row in the first place, and its "Normalized.csv"/"Exceptions.csv" output is a
single combined file across both targets. This module runs strictly AFTER
builders.build_target() and cross_reference.enrich() — i.e. against the final canonical
row a customer actually receives. A row can clear normalizer's checks (it has a real
join key/identity) and still land here, e.g. its AssetName never resolved to any source
column and no cross-reference join recovered it either.

`critical_fields` deliberately does not mean "every blank cell in the row" — many
fields are legitimately blank by this project's own design (e.g. FrequencyValue for
Rio Tinto's LAO, a documented real gap in that customer's data, not a mapping miss).
Flagging every such row would bury the genuinely actionable exceptions in noise. The
default critical set (AssetName, SerialNumber, ComponentCode, ModifierCode) is exactly
the identity + AMT-key fields this project already treats as load-bearing — the same
fields the downstream Snowflake key is built from (see core/cross_reference.py) — so a
blank there means the row can't be keyed downstream, not just that one column is thin.
By the time `find_exceptions()` runs, every both-blank ComponentCode/ModifierCode row
has already been pulled out by `split_blank_pair()` — so its own ComponentCode/
ModifierCode checks only ever catch the milder "just one of the pair is blank" case now.
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
    exceptions["_removed_from_output"] = False
    return exceptions


def split_blank_pair(out: pd.DataFrame, primary_field: str, secondary_field: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (kept, removed). `removed` holds rows where BOTH `primary_field` and
    `secondary_field` are blank (not either one alone — see module docstring), with
    `_exception_reason` and `_removed_from_output=True` already set so the caller can
    concat it straight onto find_exceptions()'s result. `kept` is everything else,
    unchanged.

    A missing field name (this target's schema doesn't have it) is a no-op — kept=out,
    removed=empty — same convention as core/exclusions.py's split_excluded().
    """
    if out.empty or primary_field not in out.columns or secondary_field not in out.columns:
        return out, out.iloc[0:0].copy()

    both_blank = _is_blank(out[primary_field]) & _is_blank(out[secondary_field])
    removed = out[both_blank].copy()
    removed["_exception_reason"] = f"missing {primary_field} and {secondary_field} (removed from output)"
    removed["_removed_from_output"] = True
    kept = out[~both_blank].copy()
    return kept, removed
