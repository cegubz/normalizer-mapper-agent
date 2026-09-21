"""Business-rule row exclusion — removes a row from NEO/LAO entirely when a configured
field (StrategyTaskDescription, by default) contains one of a configured list of
keywords, and diverts it to a companion "ExcludedComponents.csv" instead.

This is a genuinely different mechanism from everything else in the pipeline:
  - core/normalizer.py's pre-build quarantine rejects a SOURCE row before NEO/LAO exist
    at all, for structural reasons (no join key/identity, a banner row, gibberish).
  - core/exceptions.py's post-build audit flags a row that's STILL in NEO/LAO for
    review, but never removes it.
  - This module is the only one that removes an already-fully-built, otherwise-valid
    row from NEO.csv/LAO.csv — on a business-content basis (what the row is ABOUT), not
    a data-quality one. A row that matches is complete and well-mapped; it's excluded
    because this customer doesn't want that category of task in NEO/LAO at all.

Runs strictly AFTER the AMT cross-reference enrichment and the ConfidenceScore column
(core/row_confidence.py) are both in place — an excluded row keeps whatever it already
has, including its ConfidenceScore, so ExcludedComponents.csv stays a complete,
self-contained record of exactly what would have shipped. It runs BEFORE the post-build
exception audit, so an excluded row is never also flagged as an exception — those two
files are mutually exclusive.
"""
from __future__ import annotations
import re
import pandas as pd


def split_excluded(out: pd.DataFrame, field: str | None, keywords: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (kept, excluded). `excluded` holds rows whose `field` value contains any
    of `keywords` as a case-insensitive substring, plus a `_matched_keyword` column
    (the first keyword that matched, for auditability — a row can only match once even
    if several keywords would apply). `kept` is everything else, unchanged.

    A missing/absent `field`, or an empty `keywords` list, is a no-op (kept=out,
    excluded=empty) rather than an error — not every target necessarily has the
    configured field, and this module has no opinion on whether it should.
    """
    if out.empty or not field or field not in out.columns or not keywords:
        return out, out.iloc[0:0].copy()

    values = out[field].astype(str)
    matched = pd.Series([None] * len(out), index=out.index, dtype="object")
    for kw in keywords:
        hit = values.str.contains(re.escape(kw), case=False, na=False)
        matched = matched.mask(hit & matched.isna(), kw)

    excluded_mask = matched.notna()
    excluded = out[excluded_mask].copy()
    excluded["_matched_keyword"] = matched[excluded_mask]
    kept = out[~excluded_mask].copy()
    return kept, excluded
