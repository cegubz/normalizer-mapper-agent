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

The MEASUREMENT_POINTS role is the "Measurement Points" sheet: Measuring point,
Functional Location, Description of measuring point, Meas/TotCountrRdg _, Counter
reading, Annual estimate — a clean, already field-per-column layout; the deterministic
scorer typically handles this sheet well on its own (LAO_mean ~0.99), so little to no
override should be needed here.
