"""Workbook + column profiling.

Reads the customer workbook, matches sheets to roles via the flexible sheet_config,
and profiles each column so the scorer and the LLM have evidence to reason over.
No mapping decisions are made here.
"""
from __future__ import annotations
import re
import warnings
import pandas as pd

from .transforms import clean_numeric_text

warnings.filterwarnings("ignore", category=UserWarning, module="pandas")


def _norm_tokens(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]", " ", str(text).lower()).split()


def match_sheets(sheet_names: list[str], sheet_config: list[dict]) -> dict:
    """Return {role: {sheet_name, target}} using case-insensitive substring patterns.

    Flexible: driven entirely by sheet_config, so new customers/sheets need no code.
    """
    matched, unmatched_roles = {}, []
    lowered = {s: s.lower() for s in sheet_names}
    for entry in sheet_config:
        role, target = entry["role"], entry["target"]
        patterns = [p.lower() for p in entry.get("match_patterns", [])]
        hits = [s for s, low in lowered.items() if any(p in low for p in patterns)]
        if not hits:
            unmatched_roles.append(role)
            continue
        # If several tabs match, defer the tie-break to the caller (row count).
        matched[role] = {"candidates": hits, "target": target}
    return {"matched": matched, "unmatched_roles": unmatched_roles}


def read_workbook(path: str) -> dict:
    """Read all sheets into DataFrames (dropping fully-empty rows)."""
    xls = pd.ExcelFile(path)
    frames = {}
    for name in xls.sheet_names:
        df = xls.parse(name)
        frames[name] = df.dropna(how="all").reset_index(drop=True)
    return frames


def _normalize_match_text(text: str) -> str:
    """Collapse separators to single spaces for filename matching: 'Measurement-Points.csv'
    and 'measurement_points_export.csv' and the configured pattern 'measurement points'
    all compare equal, regardless of which punctuation a customer's export tool used."""
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def match_reference_files(filenames: list[str], sheet_config: list[dict]) -> dict:
    """Same shape/contract as match_sheets, but matches standalone reference-file
    basenames (e.g. blob filenames like 'LTP_2026_08.csv') against each role's
    filename_patterns — falling back to match_patterns for a config entry that hasn't
    defined filename_patterns yet, so the reference-file workflow degrades gracefully
    on an older customer config instead of failing to match anything.

    Matching is punctuation-insensitive (see _normalize_match_text) since real-world
    filenames vary in separators far more than the Excel tab names match_sheets deals
    with (LTP.csv, LTP_2026_08.csv, Measurement-Points.csv, ...).
    """
    matched, unmatched_roles = {}, []
    normalized = {f: _normalize_match_text(f) for f in filenames}
    for entry in sheet_config:
        role, target = entry["role"], entry["target"]
        patterns = [
            _normalize_match_text(p)
            for p in entry.get("filename_patterns") or entry.get("match_patterns", [])
        ]
        hits = [f for f, norm in normalized.items() if any(p in norm for p in patterns)]
        if not hits:
            unmatched_roles.append(role)
            continue
        matched[role] = {"candidates": hits, "target": target}
    return {"matched": matched, "unmatched_roles": unmatched_roles}


def read_reference_csv(path: str) -> pd.DataFrame:
    """Read a standalone reference CSV (e.g. an LTP or Measurement-Points export
    delivered as its own blob file) the same way a workbook sheet is read: drop
    fully-empty rows, reset the index. utf-8-sig transparently handles a leading BOM
    some export tools add (harmless if the file doesn't have one)."""
    df = pd.read_csv(path, encoding="utf-8-sig")
    return df.dropna(how="all").reset_index(drop=True)


def _value_evidence(series: pd.Series, dtype: str) -> float:
    s = series.dropna()
    if len(s) == 0:
        return 0.0
    if dtype == "floc":
        return float(s.astype(str).str.contains(r"-", na=False).mean())
    if dtype == "numeric":
        return float(pd.to_numeric(clean_numeric_text(s), errors="coerce").notna().mean())
    if dtype == "date":
        try:
            parsed = pd.to_datetime(s, errors="coerce", dayfirst=True, utc=True)
        except (ValueError, TypeError):
            # Garbage strings (e.g. part numbers) can fool dateutil into mixed-tz
            # guesses that to_datetime refuses to combine; treat as "not a date".
            return 0.0
        return float(parsed.notna().mean())
    if dtype == "model":
        return float(s.astype(str).str.match(r"^[0-9]{2,3}[A-Z]{0,2}$|^D[0-9]", na=False).mean())
    if dtype == "str":
        return float((s.astype(str).str.len() > 1).mean())
    return 0.5


def profile_columns(df: pd.DataFrame, sample_n: int = 8) -> list[dict]:
    """Per-column profile: header, non-null ratio, dtype evidence, and value samples.

    Skips "Unnamed" (a blank Excel header pandas auto-names) and any "_"-prefixed
    column — this project's own convention for an internal bookkeeping tag added
    somewhere in the pipeline (e.g. run_mapping_from_multiple_workbooks' own
    `_source_workbook`, or `_source_sheet`/`_reject_reason`/`_matched_keyword`/
    `_exception_reason` elsewhere). Such a column is never a real candidate source for
    a canonical field, so it shouldn't even be visible to the scorer/LLM as one.
    """
    profiles = []
    n = max(1, len(df))
    for col in df.columns:
        if str(col).startswith("Unnamed") or str(col).startswith("_"):
            continue
        s = df[col]
        samples = (
            s.dropna().astype(str).unique()[:sample_n].tolist()
        )
        profiles.append(
            {
                "column": str(col),
                "tokens": _norm_tokens(col),
                "non_null_ratio": round(float(s.notna().sum()) / n, 3),
                "evidence": {
                    k: round(_value_evidence(s, k), 3)
                    for k in ("floc", "numeric", "date", "model", "str")
                },
                "samples": samples,
            }
        )
    return profiles
