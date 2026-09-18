"""Per-ROW confidence score — a different metric from everything else in
mapping_report: `mappings[<target>][*].confidence` and `column_confidence_summary`'s
`NEO_mean`/`LAO_mean` are per-COLUMN ("how sure are we THIS source column is the right
one for THIS canonical field?") — the same number for every row that uses it. This one
answers a different question per INDIVIDUAL row: "of what's actually printed in this
one row, how much of it rests on solid evidence?"

A column can be mapped at 0.95 confidence and a specific row can still have a genuinely
blank cell there (that row's own source cell was empty) — that row gets no credit for a
field it doesn't actually have data for, no matter how good the column mapping is
elsewhere in the same column.

Only fields the pipeline actually attempts for this customer shape feed the score:
  - `customer_file` fields are a heuristic guess about which column holds the data —
    their trust is that field's own computed column-mapping confidence (the same number
    already in `mappings[<target>]`), even when that confidence is low or the pick was
    rejected to null — the model DID reason about it, so it belongs in the row's story.
  - `constant`/`derived` fields aren't a guess at all — a literal config value or a pure
    computed transform. Trust = 1.0 when present.
  - A plain `enrichment` field (e.g. BranchCode/SiteCode) that this customer shape's AMT
    cross-reference pass has no mechanism to ever fill is EXCLUDED from the score
    entirely, not zeroed — it's permanently out of scope by this project's original
    design (see the main README), unrelated to any one row's quality, so it shouldn't
    drag every row down by the same fixed amount. The moment the cross-reference pass
    *does* fill an enrichment field for at least one row of this shape (e.g. FMG's
    ComponentCode), mapping_engine.py adds it to the score at trust 0.95 (an exact
    deterministic key-join match, more reliable than a rejected column-alias guess) —
    and then it varies genuinely row-to-row: 0.95 on the rows it filled, 0 on the ones
    it didn't.

Either way, a blank cell on an INCLUDED field always contributes 0 — trust in a mapping
only matters for data that's actually there. See compute()'s docstring for exactly how
inclusion vs. zeroing is decided, and mapping_engine.py for how field_confidence (this
function's third argument) is built.
"""
from __future__ import annotations
import pandas as pd

_BLANK_TOKENS = ("", "nan", "none", "null")


def _is_filled(series: pd.Series) -> pd.Series:
    return ~(series.isna() | series.astype(str).str.strip().str.lower().isin(_BLANK_TOKENS))


def compute(out: pd.DataFrame, output_columns: list[str], field_confidence: dict) -> pd.Series:
    """Row score = mean, across `output_columns` that appear as a key in
    `field_confidence`, of field_confidence[col] where the row's own cell for that
    column is filled, else 0.0.

    A column ABSENT from `field_confidence` is excluded from the average entirely, not
    zeroed — that's deliberate, and it's the difference between two very different
    kinds of blank: an `enrichment` field this customer shape's cross-reference pass has
    no mechanism to ever fill (e.g. BranchCode/SiteCode — permanently out of scope for
    every row, by this project's original design, nothing to do with THIS row's quality)
    should not drag down a "how confident is the model about this row" score at all; a
    field the pipeline *did* attempt (customer_file, constant, derived, or an enrichment
    field the AMT join fills for at least some rows of this shape) belongs in it, and
    contributes 0 on the specific rows where that attempt came up empty. See
    mapping_engine.py's field_confidence construction for which fields end up as keys.
    """
    if out.empty or not output_columns:
        return pd.Series([], index=out.index, dtype="float64")

    contributions = [
        _is_filled(out[col]).astype(float) * field_confidence[col]
        for col in output_columns
        if col in out.columns and col in field_confidence
    ]
    if not contributions:
        return pd.Series([0.0] * len(out), index=out.index)

    return pd.concat(contributions, axis=1).mean(axis=1).round(3)
