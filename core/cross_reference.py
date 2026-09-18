"""AMT cross-reference enrichment — fills ComponentCode / ModifierCode / SerialNumber
(and, for LAO, AssetName) from the per-workbook-shape lookup tables in
prompts/cross-references/, for the shapes where the customer file itself doesn't
already carry them. These three fields (SerialNumber + ComponentCode + ModifierCode)
are the composite key the downstream Snowflake population job joins on, so a genuine
gap here isn't just a cosmetic blank cell — it's a row Snowflake can't key at all.

This runs strictly AFTER builders.build_target(), and only ever fills a cell that is
still blank — it never overwrites a value the customer file itself provided. When no
cross-reference row matches (or, for FMG's LAO fallback below, when the lookup key is
genuinely ambiguous), the cell stays blank, same as before: the project's existing
non-goal ("no invented columns/values") applies here exactly as it does everywhere else
in the pipeline.

Each customer workbook shape needs a different join key because the source data is
shaped differently (see prompts/appendix.*.md for the verified column layouts):
  - FMG (CB MM LTP AUGUST): NEO's Group(L) + the functional-location segments after
    <plant>-MP-<area>-<equipment> looked up against fmg_cross-reference.csv's
    Group_CompFuncLoc key fills ComponentCode/ModifierCode there. Measurement Points
    (LAO) has no Group(L) column at all, so it can't reproduce that same key — instead
    it falls back to the functional-location suffix ALONE, matched against a version of
    the same CSV collapsed to unique (Component_Code, Modifier_Code) pairs per suffix
    (see _index_csv_unique_by). A suffix that maps to more than one distinct pair across
    different Group(L) values in the source table is a genuine ambiguity (no way to
    tell which one a given LAO row means without Group(L)) and is deliberately left
    blank rather than guessed. AssetName is also derived from the functional location
    itself. There is no full Serial Number anywhere in this shape's source data or in
    fmg_cross-reference.csv (only a "Serial Prefix" — a model-family code, not a
    per-unit serial) — SerialNumber is a genuine, unfillable gap for this shape, on
    both NEO and LAO — verified against test-data/CB MM LTP AUGUST.xlsx +
    Measurement-Points.csv.
  - Rio Tinto: Comp Grid (NEO) already carries Component Code / Modifier Code / Serial
    Number directly as real columns (mapped via customer_file, not this module) — no
    cross-reference needed there. IK07 (LAO) carries blank CC/MC for most rows and has
    no Serial Number column at all (only "Serial prefix"); Serial prefix + Func Location
    looked up against rio-tinto_cross-reference.csv's "FuncLoc Key" recovers
    ComponentCode/ModifierCode/SerialNumber together (all three live on the same
    cross-reference row), and Eq ID supplies AssetName directly — verified against
    test-data/New Workfile Rio Tinto Aug 2026.xlsx.
  - BHP/Westrac (Billiton sheet): NEO only. Component Code / Modifier Code / Serial
    Number are documented as real columns on this sheet (mapped via customer_file, not
    this module, when present). Where they're genuinely blank, Model + part number
    looked up against bhp_cross-reference.csv's CONCAT2 key lets the compound "AMT"
    string ("1361 - WATER PUMP-00 - (NONE)") be parsed into ComponentCode/ModifierCode
    (bhp_cross-reference.csv has no Serial Number column, so that field has no
    cross-reference fallback here — it relies solely on the sheet's own column).
    UNVERIFIED against a real Billiton workbook — none exists in test-data/; this path
    is best-effort from the cross-reference file's own structure and
    prompts/appendix.bhp.md's documented column list, not confirmed against real data
    like the other two. This shape has no LAO/measurement-points sheet at all.
"""
from __future__ import annotations
import re
from functools import lru_cache

import pandas as pd

from .settings import ROOT

XREF_DIR = ROOT / "prompts" / "cross-references"


@lru_cache(maxsize=8)
def _load_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(XREF_DIR / name, dtype=str, keep_default_na=False, encoding="utf-8-sig")


@lru_cache(maxsize=8)
def _index_csv(name: str, key_col: str) -> dict:
    """{key_column value: row as dict}, first row wins on duplicate keys."""
    df = _load_csv(name)
    index: dict = {}
    for row in df.to_dict(orient="records"):
        key = row.get(key_col, "")
        if key and key not in index:
            index[key] = row
    return index


@lru_cache(maxsize=8)
def _index_csv_unique_by(name: str, key_col: str, value_cols: tuple[str, ...]) -> dict:
    """Like _index_csv, but a key is only included when every row sharing it agrees on
    `value_cols` — an ambiguous key (the same key mapping to more than one distinct
    combination of values elsewhere in the table) is left out entirely rather than
    picking an arbitrary row for it.

    Used for FMG's LAO fallback: the cross-reference's real key is Group(L) + floc
    suffix, but Measurement Points has no Group(L) column, so the suffix alone is all
    that's available there. Most suffixes still resolve to exactly one component
    regardless of Group(L); the ones that don't are a genuine "can't tell without more
    context" case, not a mapping miss, so returning nothing for them is the honest
    answer rather than a guess that's right ~50/50.
    """
    df = _load_csv(name)
    combos_by_key: dict[str, set] = {}
    row_by_key: dict[str, dict] = {}
    for row in df.to_dict(orient="records"):
        key = row.get(key_col, "")
        if not key:
            continue
        combos_by_key.setdefault(key, set()).add(tuple(row.get(c, "") for c in value_cols))
        row_by_key.setdefault(key, row)
    return {k: row_by_key[k] for k, combos in combos_by_key.items() if len(combos) == 1}


def _find_column(df: pd.DataFrame, *needles: str) -> str | None:
    """First column whose lowercased header contains any of `needles`."""
    for col in df.columns:
        low = str(col).lower()
        if any(n in low for n in needles):
            return col
    return None


def _int_str(v) -> str:
    """'90028980.0' -> '90028980' (Excel reads whole-number ID columns as float)."""
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return str(v).strip() if v is not None else ""


def _floc_segments(floc) -> list[str]:
    if floc is None or (isinstance(floc, float) and pd.isna(floc)):
        return []
    return [s.strip() for s in str(floc).split("-")]


def _floc_suffix_after_equipment(floc) -> str:
    """For a '<plant>-MP-<area>-<equipment>-<component>-<sub>...' functional location,
    return the segments after the equipment token (index 3), joined back with '-'.
    Empty string when there's no component suffix (a bare equipment-level floc) or the
    string doesn't have enough segments to contain one.
    """
    segs = _floc_segments(floc)
    return "-".join(segs[4:]) if len(segs) > 4 else ""


def _equipment_from_floc(floc) -> str:
    """The equipment token itself (index 3) from the same floc shape."""
    segs = _floc_segments(floc)
    return segs[3] if len(segs) > 3 else ""


def _fill_blank(out: pd.DataFrame, col: str, values: pd.Series) -> int:
    """Fill out[col] from `values` only where out[col] is currently blank and `values`
    has something to offer. Returns the number of cells actually filled.

    "Blank" covers both a wholly-unmapped field (builders.py writes "" when
    source_column was None) and a real, partially-populated customer_file column (e.g.
    Rio Tinto's IK07 "CC" is a genuine column that is simply NaN on most rows) — a plain
    `astype(str) == ""` check misses the latter, since `str(nan) == "nan"`.
    """
    if col not in out.columns:
        return 0
    current = out[col]
    current_blank = current.isna() | (current.astype(str).str.strip() == "")
    incoming = values.reindex(out.index)
    incoming_blank = incoming.isna() | (incoming.astype(str).str.strip().isin(("", "nan")))
    fillable = current_blank & ~incoming_blank
    if fillable.any():
        out.loc[fillable, col] = incoming[fillable]
    return int(fillable.sum())


_AMT_RE = re.compile(r"^\s*(?P<cc>\d+)\s*-\s*(?P<desc>.+?)-(?P<mc>\S+)\s*-\s*\([^)]*\)\s*$")


def _parse_amt(amt) -> tuple[str, str]:
    """'1361 - WATER PUMP-00 - (NONE)' -> ('1361', '00'). Best-effort: returns ('','')
    for anything that doesn't match the observed BHP AMT-string shape.
    """
    m = _AMT_RE.match(str(amt)) if amt else None
    return (m.group("cc"), m.group("mc")) if m else ("", "")


# ---------------------------------------------------------------------------
# Per-(prompt_variant, target) enrichers. Each takes the already-built output frame
# (`out`, still holding blanks for anything unresolved) and the clean source frame
# (`clean_df`, same index as `out`) plus `resolved` ({canonical: source_column} for
# customer_file fields), and fills blanks in place. Returns {canonical: cells_filled}
# for the mapping_report to note.
# ---------------------------------------------------------------------------

def fmg_neo(out: pd.DataFrame, clean_df: pd.DataFrame, resolved: dict) -> dict:
    group_col = _find_column(clean_df, "group (l)", "group l")
    floc_col = resolved.get("FunctionalLoc")
    if not group_col or not floc_col or floc_col not in clean_df.columns:
        return {}

    xref = _index_csv("fmg_cross-reference.csv", "Group_CompFuncLoc")
    join_key = clean_df[group_col].map(_int_str) + clean_df[floc_col].map(_floc_suffix_after_equipment)
    hits = join_key.map(xref.get)

    cc_filled = _fill_blank(out, "ComponentCode", hits.map(lambda h: h.get("Component_Code", "") if h else ""))
    mc_filled = _fill_blank(out, "ModifierCode", hits.map(lambda h: h.get("Modifier_Code", "") if h else ""))
    return {"ComponentCode": cc_filled, "ModifierCode": mc_filled}


def fmg_lao(out: pd.DataFrame, clean_df: pd.DataFrame, resolved: dict) -> dict:
    """AssetName comes straight out of the functional location. ComponentCode/
    ModifierCode use a suffix-only fallback (see _index_csv_unique_by's docstring for
    why): unlike NEO, Measurement Points has no Group(L) to reproduce the real
    Group_CompFuncLoc key, so only the subset of suffixes that resolve to exactly one
    component regardless of Group(L) can be filled here — verified against
    test-data/CB MM LTP AUGUST.xlsx + Measurement-Points.csv: of the cross-reference
    table's 501 unique functional-location suffixes, 385 resolve to a single
    Group(L)-independent (Component_Code, Modifier_Code) combination, but only 225 of
    those 385 actually carry a non-blank code — the rest are genuine gaps in the source
    table itself (rows marked e.g. "Reason: Not identified" / "Not planned by
    Westrac"), not something a better join could recover. Net effect: 2,975 / 22,893
    Measurement Points rows (~13%) get a real ComponentCode/ModifierCode this way; the
    remaining suffixes are either ambiguous (left blank on purpose — ~18% of rows) or
    match nothing in the table at all (~51%) or resolve to a table row with no code
    (~18%). SerialNumber has no source here at all — this shape has no full serial
    number anywhere, only a "Serial Prefix" family code — so it isn't attempted.
    """
    floc_col = resolved.get("FunctionalLoc")
    if not floc_col or floc_col not in clean_df.columns:
        return {}
    filled = {}

    equip = clean_df[floc_col].map(_equipment_from_floc)
    filled["AssetName"] = _fill_blank(out, "AssetName", equip)

    suffix = clean_df[floc_col].map(_floc_suffix_after_equipment)
    xref = _index_csv_unique_by(
        "fmg_cross-reference.csv", "Functional_Loc", ("Component_Code", "Modifier_Code")
    )
    hits = suffix.map(xref.get)
    filled["ComponentCode"] = _fill_blank(
        out, "ComponentCode", hits.map(lambda h: h.get("Component_Code", "") if h else "")
    )
    filled["ModifierCode"] = _fill_blank(
        out, "ModifierCode", hits.map(lambda h: h.get("Modifier_Code", "") if h else "")
    )
    return filled


def rio_tinto_lao(out: pd.DataFrame, clean_df: pd.DataFrame, resolved: dict) -> dict:
    """Deliberately does NOT use resolved["FunctionalLoc"] — IK07 carries three
    near-duplicate floc columns ("Functional Loc.", "Func Location", "Func Loc"), and
    the scorer/LLM picks whichever best matches the canonical FunctionalLoc field's
    aliases for its own (unrelated) purposes; that pick is "Functional Loc." in
    practice, a compound plant+equipment+floc string that does NOT match the
    cross-reference's key format at all. IK07 already carries its own precomputed
    "Func Loc Key" (= Serial prefix + "Func Location", verified to equal that
    reconstruction on every non-null row in test-data/New Workfile Rio Tinto Aug
    2026.xlsx) — use that directly instead of re-deriving it from a canonical field
    that means something different here.
    """
    filled = {}
    key_col = _find_column(clean_df, "func loc key")
    if key_col:
        xref = _index_csv("rio-tinto_cross-reference.csv", "FuncLoc Key")
        hits = clean_df[key_col].fillna("").astype(str).map(xref.get)
    else:
        prefix_col = _find_column(clean_df, "serial prefix", "serial pfx")
        floc_col = _find_column(clean_df, "func location")
        hits = None
        if prefix_col and floc_col:
            xref = _index_csv("rio-tinto_cross-reference.csv", "FuncLoc Key")
            join_key = clean_df[prefix_col].fillna("").astype(str) + clean_df[floc_col].fillna("").astype(str)
            hits = join_key.map(xref.get)

    if hits is not None:
        filled["ComponentCode"] = _fill_blank(
            out, "ComponentCode", hits.map(lambda h: h.get("Final CC", "") if h else "")
        )
        filled["ModifierCode"] = _fill_blank(
            out, "ModifierCode", hits.map(lambda h: h.get("Final MC", "") if h else "")
        )
        filled["SerialNumber"] = _fill_blank(
            out, "SerialNumber", hits.map(lambda h: h.get("Serial Number", "") if h else "")
        )

    eq_col = _find_column(clean_df, "eq id")
    if eq_col:
        filled["AssetName"] = _fill_blank(out, "AssetName", clean_df[eq_col])

    return filled


def bhp_neo(out: pd.DataFrame, clean_df: pd.DataFrame, resolved: dict) -> dict:
    model_col = resolved.get("ModelCode")
    part_col = _find_column(clean_df, "primary part number", "bhp part no", "part number", "material")
    if not model_col or model_col not in clean_df.columns or not part_col:
        return {}

    xref = _index_csv("bhp_cross-reference.csv", "CONCAT2")
    join_key = clean_df[model_col].fillna("").astype(str).str.strip() + clean_df[part_col].fillna("").astype(str).str.strip()
    hits = join_key.map(xref.get)
    parsed = hits.map(lambda h: _parse_amt(h.get("AMT", "")) if h else ("", ""))

    cc_filled = _fill_blank(out, "ComponentCode", parsed.map(lambda t: t[0]))
    mc_filled = _fill_blank(out, "ModifierCode", parsed.map(lambda t: t[1]))
    return {"ComponentCode": cc_filled, "ModifierCode": mc_filled}


ENRICHERS = {
    ("fmg", "NEO"): fmg_neo,
    ("fmg", "LAO"): fmg_lao,
    ("rio_tinto", "LAO"): rio_tinto_lao,
    ("bhp", "NEO"): bhp_neo,
}


def enrich(target: str, prompt_variant: str | None, out: pd.DataFrame, clean_df: pd.DataFrame, resolved: dict) -> dict:
    """Fill blanks in `out` (in place) for the (prompt_variant, target) pair, if a
    dedicated enricher exists. Returns {canonical: cells_filled} — empty when no
    enricher applies (unrecognized workbook shape, or nothing left to fill) or the
    prerequisite source column(s) weren't found for this run's actual data.
    """
    fn = ENRICHERS.get((prompt_variant, target))
    if not fn or clean_df.empty:
        return {}
    return fn(out, clean_df, resolved)
