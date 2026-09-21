"""Keep-latest-date deduplication — for rows that represent revisions of "the same"
task (same values across a configured `group_by` set of canonical fields — e.g. the
same asset + component + modifier + task-counter slot, just planned on different
dates), keep only the row with the latest `date_field` value and remove the rest.

Configured per target via `deduplication.<target>` in the customer JSON. A target with
no entry there, or whose `date_field` isn't a real, populated field for that shape
(e.g. LAO has no start-date-equivalent field in this project today — see README), is a
deliberate no-op: this rule only fires where there's an actual date to compare rows on.
"""
from __future__ import annotations
import pandas as pd


def split_duplicates(
    out: pd.DataFrame, group_by: list[str], date_field: str | None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (kept, removed). Within each group of rows sharing identical values for
    every column in `group_by`, a row is removed only if it has a real `date_field`
    value AND that value is strictly older than the latest dated value in its group.

    Two things are deliberately NOT removed:
      - A row with a blank/unparseable `date_field` — there's nothing to compare, so
        this rule has no basis to call it a stale duplicate.
      - A row tied for the latest date within its group — "retain the rows with latest
        StartDate" (plural) is read literally: a genuine tie means more than one row
        legitimately IS the latest, not that this function should arbitrarily pick one.
    """
    if out.empty or not date_field or date_field not in out.columns or not group_by:
        return out, out.iloc[0:0].copy()
    if not all(c in out.columns for c in group_by):
        return out, out.iloc[0:0].copy()

    dates = pd.to_datetime(out[date_field], errors="coerce")
    has_date = dates.notna()

    group_keys = [out[c] for c in group_by]
    group_max = dates.where(has_date).groupby(group_keys).transform("max")
    is_latest = has_date & (dates == group_max)
    removed_mask = has_date & ~is_latest

    kept = out[~removed_mask].copy()
    removed = out[removed_mask].copy()
    return kept, removed
