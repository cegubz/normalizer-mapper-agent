"""Output builders for NEO / LAO.

Given a clean source frame + the resolved mapping + the target config, build the fixed
output schema. customer_file columns are mapped (with dtype transform), constants filled,
derived columns computed, and enrichment columns left BLANK (never invented).
"""
from __future__ import annotations
import pandas as pd
from .transforms import COLUMN_TRANSFORMS, ROW_TRANSFORMS


_DTYPE_DEFAULT_TRANSFORM = {
    "date": "to_yyyymmdd",
    "numeric": "to_numeric",
}


def build_target(
    clean_df: pd.DataFrame,
    target_cfg: dict,
    resolved: dict,
    constants: dict,
) -> pd.DataFrame:
    """resolved = {canonical: source_column or None} for customer_file fields."""
    out = pd.DataFrame(index=clean_df.index)
    fields = {f["canonical"]: f for f in target_cfg["fields"]}

    # 1) customer_file fields (+ intermediate canonicals like MeasTotCtr used by derived)
    for canonical, f in fields.items():
        if f.get("source_class") != "customer_file":
            continue
        src = resolved.get(canonical)
        if src is None or src not in clean_df.columns:
            out[canonical] = ""
            continue
        series = clean_df[src]
        tname = f.get("transform") or _DTYPE_DEFAULT_TRANSFORM.get(f.get("dtype", "str"))
        if tname and tname in COLUMN_TRANSFORMS:
            series = COLUMN_TRANSFORMS[tname](series)
        out[canonical] = series

    # 2) constants
    for canonical, f in fields.items():
        if f.get("source_class") == "constant":
            out[canonical] = constants.get(canonical, "")

    # 3) derived (may depend on customer_file or on blank enrichment columns)
    for canonical, f in fields.items():
        if f.get("source_class") != "derived":
            continue
        tname = f.get("transform")
        deps = f.get("depends_on", [])
        # Ensure dependency columns exist (blank if enrichment / unresolved)
        for d in deps:
            if d not in out.columns:
                out[d] = ""
        if tname in ROW_TRANSFORMS:
            out[canonical] = ROW_TRANSFORMS[tname](out, deps)
        else:
            out[canonical] = ""

    # 4) enrichment + any remaining output columns -> blank (never invented)
    for canonical in target_cfg["output_columns"]:
        if canonical not in out.columns:
            out[canonical] = ""

    # 5) select + order exactly to the fixed output schema
    out = out.reindex(columns=target_cfg["output_columns"])
    return out
