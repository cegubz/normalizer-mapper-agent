"""Named, declarative transforms referenced by customer config.

These implement ONLY the deterministic logic already present in NEO.py / LAO.py.
No new workflow is introduced. Add a new transform here and reference it by name in
a customer config field ("transform": "<name>").
"""
from __future__ import annotations
import pandas as pd


def to_yyyymmdd(series: pd.Series) -> pd.Series:
    # utc=True avoids "Mixed timezones detected" if the column mixes tz-aware and
    # naive values; naive values are simply localized to UTC (no wall-clock shift).
    return pd.to_datetime(series, errors="coerce", dayfirst=True, utc=True).dt.strftime("%Y%m%d")


def clean_numeric_text(series: pd.Series) -> pd.Series:
    """Strip thousands-separator commas and stray padding whitespace (e.g. ' 20,000 ')
    before numeric parsing. Needed whenever a number arrives as formatted text — always
    true for a standalone CSV reference file (see REFERENCE_FILES.md), and sometimes
    true even from a workbook cell exported as text. A genuinely numeric dtype series
    passes through unchanged. Shared with profiling._value_evidence so the scorer's
    numeric-evidence signal and this transform never disagree about what "numeric"
    means for the same column.
    """
    if pd.api.types.is_numeric_dtype(series):
        return series
    return series.astype("string").str.strip().str.replace(",", "", regex=False)


def to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(clean_numeric_text(series), errors="coerce")


def as_str(series: pd.Series) -> pd.Series:
    return series.astype("string")


def split_dash_first(series: pd.Series) -> pd.Series:
    # e.g. "1000-ENGINE" -> "1000" (used for Component/Task codes in the source pipeline)
    return series.astype("string").str.split("-").str[0].str.strip()


# ---- derived (row-wise, computed from already-resolved canonical columns) ----

def meas_minus_counter(df: pd.DataFrame, deps: list[str]) -> pd.Series:
    """LAO LastStrategyUsageValue = Meas/TotCtr - Counter reading (LAO.py)."""
    a = pd.to_numeric(df[deps[0]], errors="coerce")
    b = pd.to_numeric(df[deps[1]], errors="coerce")
    return a - b


def sales_lost_reason(df: pd.DataFrame, deps: list[str]) -> pd.Series:
    """'Other' where SalesStatus contains 'Lost', else '' (NEO.py / LAO.py)."""
    col = deps[0]
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index)
    return df[col].astype("string").fillna("").apply(lambda x: "Other" if "Lost" in str(x) else "")


def source_of_supply(df: pd.DataFrame, deps: list[str]) -> pd.Series:
    """SOS code from primary part number (NEO.py / LAO.py rules).

    Enrichment-dependent: if the part number column is blank (not yet joined), returns
    blank rather than inventing a code.
    """
    col = deps[0]
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index)
    p = df[col].astype("string").fillna("")

    def rule(v: str) -> str:
        if v == "":
            return ""
        if ("X" not in v) and ("F" not in v):
            return "000"
        if v.endswith("F"):
            return "483"
        if len(v) >= 4 and "X" in v[-4:]:
            return "503"
        return "000"

    return p.apply(rule)


COLUMN_TRANSFORMS = {
    "to_yyyymmdd": to_yyyymmdd,
    "to_numeric": to_numeric,
    "as_str": as_str,
    "split_dash_first": split_dash_first,
}

ROW_TRANSFORMS = {
    "meas_minus_counter": meas_minus_counter,
    "sales_lost_reason": sales_lost_reason,
    "source_of_supply": source_of_supply,
}
