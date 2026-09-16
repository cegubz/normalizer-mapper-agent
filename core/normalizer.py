"""Row-level normalization / quarantine (the 'Normalized.csv' workflow).

Splits a source sheet into (clean_rows, rejected_rows) using the resolved mapping.
Rejected rows keep their original columns plus _source_sheet and _reject_reason.
Only the reject rules already established in the project are applied.
"""
from __future__ import annotations
import re
import pandas as pd

_GIBBERISH = re.compile(r"^[^A-Za-z0-9]{1,2}$")


def _is_gibberish_row(row: pd.Series, key_col, id_col) -> bool:
    """A row whose key/identity are present but are single junk characters."""
    vals = [row.get(key_col), row.get(id_col)]
    checked = [str(v) for v in vals if pd.notna(v)]
    return bool(checked) and all(_GIBBERISH.match(v or "") for v in checked)


def split_clean_rejected(
    df: pd.DataFrame,
    sheet_name: str,
    key_col,
    id_col,
    reject_rules: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    reasons = pd.Series([None] * len(df), index=df.index, dtype="object")

    if "missing_join_key" in reject_rules and key_col in df.columns:
        mask = df[key_col].isna()
        reasons = reasons.mask(mask & reasons.isna(), f"missing join key ({key_col})")

    if "missing_identity" in reject_rules and id_col in df.columns and id_col != key_col:
        mask = df[id_col].isna()
        reasons = reasons.mask(mask & reasons.isna(), f"missing identity ({id_col})")

    if "banner_row" in reject_rules:
        key_series = df[key_col] if key_col in df.columns else pd.Series([None] * len(df))
        id_series = df[id_col] if id_col in df.columns else pd.Series([None] * len(df))
        banner = key_series.isna() & id_series.isna()
        reasons = reasons.mask(banner & reasons.isna(), "banner/section row (no key or identity)")

    if "gibberish" in reject_rules:
        gib = df.apply(lambda r: _is_gibberish_row(r, key_col, id_col), axis=1)
        reasons = reasons.mask(gib & reasons.isna(), "gibberish key/identity value")

    rejected_mask = reasons.notna()
    rejected = df[rejected_mask].copy()
    rejected["_source_sheet"] = sheet_name
    rejected["_reject_reason"] = reasons[rejected_mask].values

    clean = df[~rejected_mask].copy()
    return clean, rejected
