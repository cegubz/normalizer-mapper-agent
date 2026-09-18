====================================================================
KNOWN WORKBOOK CONTEXT — CB MM LTP AUGUST.xlsx (verify against THIS run's real data;
column order/content can change between exports, so treat this as a strong prior, not
a substitute for actually checking `columns`/`samples` below)
====================================================================
This workbook's LTP role is the "CB MM LTP" sheet, columns lettered (A)-(Q) plus a few
unlettered ones: Type (A), Program (B), Department (C), Vendor (D), Equipment (E),
Task Name (F), Start (G), Finish (H), Duration (I), Period (J), Work (K), Group (L),
Group Counter (M), Maint Item (N), Component Due Date (O), Strategy (P), Function
Location (Q), Annual Estimate, Meas/TotCtrRdg, Counter reading.

Fields that have previously needed the model to override a weak deterministic pick,
and what was verified to be true of them:
  - ModelCode: Type (A) — values like "785D", "777F", "24M", "994H", "D11T" are
    Caterpillar-style machine model codes.
  - AssetName: Equipment (E) — values like "WC012", "GR018", "SV021" are per-unit
    asset IDs (prefix generally indicates fleet/site).
  - TaskCounterCode: Group Counter (M) — short alphanumeric sequence codes ("A1", "1",
    "2", "A3"), a recurring per-component counter, not a one-off transaction ID.
  - StrategyTaskDescription: Task Name (F) — free-text description of the
    maintenance activity/schedule; some values look like generic program labels
    ("MCORS" = Major Component Replacement Schedule) rather than per-row unique text —
    that is expected for this column, not a sign it's the wrong one.
  - FrequencyValue: Strategy (P) — mostly numeric hour intervals (20000, 10000,
    24000), occasionally a text cadence like "12 Monthly "; both are legitimate
    frequency expressions for this field.
  - StrategyDate: Component Due Date (O) — DD.MM.YYYY due dates.
  - NewStrategyDate: Start (G) — ISO planned-start dates, genuinely distinct from
    Component Due Date (O); this file (unlike some others) does carry a real second
    date, so a low-confidence null here is likely wrong for this workbook specifically.
  - FunctionalLoc: Function Location (Q) — hyphenated hierarchical FLOC strings
    (e.g. "CLB-MP-SUPP-WC012-ENGINE"); this file has genuine Functional Location data,
    unlike some other customers' exports.
  - ComponentCode / ModifierCode: this sheet genuinely has NO component-code or
    modifier-code column of any kind — verified against the full column list (Type,
    Program, Department, Vendor, Equipment, Task Name, Start, Finish, Duration, Period,
    Work, Group (L), Group Counter, Maint Item, Component Due Date, Strategy, Function
    Location, Annual Estimate, Meas/TotCtrRdg, Counter reading). Return `source_column:
    null` for both, honestly — do not be tempted by "Group (L)" or "Group Counter (M)"
    just because they're short codes near component-shaped fields; they're grouping/
    sequence IDs, not component codes. A separate AMT cross-reference join in code
    (core/cross_reference.py, using Group (L) + the functional-location suffix as the
    key — a mechanism this call has no visibility into and should not try to replicate)
    fills ~43% of NEO rows afterward from a lookup table; the rest are genuine gaps in
    that table, not something a better column pick here could recover.
  - SerialNumber: this sheet genuinely has NO serial-number column, and the
    cross-reference table used for ComponentCode/ModifierCode above has no full serial
    number either — only a "Serial Prefix" (a model-family code like "RJG", not a
    per-unit serial). This is a real, permanent gap for this workbook shape, on both
    NEO and Measurement Points — return `source_column: null` rather than guessing at
    any "Group"/ID-shaped column.

The MEASUREMENT_POINTS role is the "Measurement Points" sheet: Measuring point,
Functional Location, Description of measuring point, Meas/TotCountrRdg _, Counter
reading, Annual estimate — a clean, already field-per-column layout; the deterministic
scorer typically handles this sheet well on its own for the fields it actually has.
This sheet has no equipment-ID, component-code, modifier-code, or serial-number column
at all — the equipment number is recovered afterward in code by parsing it out of the
Functional Location string itself (the segment right after `<plant>-MP-<area>-`), and
ComponentCode/ModifierCode are recovered for ~13% of rows (2,975 / 22,893 in the
verified run) the same way, via a suffix-only, best-effort AMT cross-reference lookup —
see core/cross_reference.py's fmg_lao — not by this call; return `source_column: null`
for AssetName/ComponentCode/ModifierCode/SerialNumber here rather than guessing at
Measuring point or Functional Location for them. SerialNumber specifically has no
recovery path at all for this shape (see the NEO note above) — it stays null
everywhere for FMG.
