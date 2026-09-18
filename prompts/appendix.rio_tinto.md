====================================================================
KNOWN WORKBOOK CONTEXT — New Workfile Rio Tinto Aug 2026.xlsx (verify against THIS
run's real data; column order/content can change between exports, so treat this as a
strong prior, not a substitute for actually checking `columns`/`samples` below)
====================================================================
This workbook has a sheet literally named "LTP" that is a RAW SAP extract (Vendor ID,
Material, Task List...) — NOT the source sheet. The real source for the LTP role is a
sheet called "Comp Grid": a clean, already field-per-column layout.

Comp Grid columns include: Branch, Site, Model, Equipment, Component Code, Modifier
Code, Frequency, Life To Date, Strategy Date, Primary Part Number, Sales Status, PO
Number, Comments, Fleet, Next Part Number Required, Next Part Classification, Task
Type, Strategy Usage, ST Description, Value, Sales Responsibility, Customer, Serial
Number, Task Counter, Life Remaining, Eq ID, Serial Prefix, AMT Key, AMT Key2, and
several "*Match?" flag columns.

Fields verified against Comp Grid:
  - ModelCode: Model — values like "6060 BH", "16M", "24H".
  - AssetName: Equipment — values like "15H402 - 6D600101" (unit ID + serial pair).
  - ComponentCode: "Component Code" — values like "5085 - PILOT PUMP", ~82% populated
    (12,376 / 14,999 rows in the verified run). This is a real, literal column here —
    map it directly, do not treat it as unavailable.
  - ModifierCode: "Modifier Code" — values like "LR - LEFT REAR", same column pairing
    and population rate as Component Code above.
  - SerialNumber: "Serial Number" — values like "F5200103", a real, literal, fully
    populated column here, distinct from "Serial Prefix" (a shorter family code like
    "F520" — the first few characters of the serial, not the serial itself). Map
    SerialNumber to "Serial Number" directly; do not substitute "Serial Prefix" for it.
  - TaskCounterCode: Task Counter — values like "0 - (NONE)", "1 - 1", "01 - 01".
  - StrategyTaskDescription: ST Description — values like "5085.LR.RB.0 PILOT PUMP".
  - FrequencyValue: Frequency — numeric hour intervals (7500.0, 12000.0, 15000.0).
  - StrategyDate: Strategy Date — real datetimes.
  - FunctionalLoc: Comp Grid genuinely has NO functional-location code column —
    Branch/Site/Fleet are grouping labels (e.g. "Rio-Marandoo-Excavators"), not
    per-asset FLOC codes. This has repeatedly, correctly scored low (~0.40-0.55).
    Only override this if THIS run's actual samples show a real hyphenated FLOC
    pattern somewhere — otherwise return null/low confidence; do not force a guess.
  - NewStrategyDate: Comp Grid genuinely has no second date distinct from Strategy
    Date. Treat as a real gap unless this run's data shows otherwise.

The MEASUREMENT_POINTS role is the "IK07" sheet, a raw SAP measuring-point reading log:
Description, Functional Loc., Measuring point, Date, Component Hours, Total Machine
Hours, Difference, CountrReplaced, Text, MeasDocument, Created by, CharactUnit, Created
on, Eq ID, Serial prefix, Xref Key, Func Location, Func Loc Key, CC, MC, AMT Key, LAO
Date, Asset in AMT?, Func Loc.

Fields verified against IK07:
  - FunctionalLoc: "Functional Loc." (or "Func Location"/"Func Loc") carries real FLOC
    values — this field usually scores well here (~0.9+); pick whichever of the near-
    duplicate Func Loc columns actually holds the fullest/most specific code. Note this
    canonical field is used for the LTP join-key bonus and general FLOC reporting —
    it is NOT what the AMT cross-reference enrichment uses internally for its own key
    (that logic reads "Func Loc Key"/"Serial prefix" directly out of the sheet, exactly
    because "Functional Loc." is a compound plant+equipment+floc string with a
    different shape than the cross-reference table expects; see
    core/cross_reference.py's rio_tinto_lao docstring for why).
  - AssetName: "Eq ID" — a plain per-asset unit ID (e.g. "06H216"), always present
    alongside Serial prefix. IK07 has no compound "unit - serial" style Equipment
    column like Comp Grid does — Eq ID is the only equipment identifier here.
  - ComponentCode / ModifierCode: "CC"/"MC" and "CC FL"/"MC FL" are two pairs of
    near-duplicate columns. "CC FL"/"MC FL" is the better one — in the verified run it
    was populated on 13,757 / 59,999 rows vs. 13,254 for plain "CC"/"MC", and the two
    pairs agree wherever both have a value (the "FL" pair is a superset). The
    remaining gap (most rows: this is a reading log, not a pre-mapped sheet — under
    23% of rows have a component context at all) is filled where possible by the AMT
    cross-reference enrichment step in code (core/cross_reference.py), which
    independently reproduces the same 13,757-row coverage via "Func Loc Key" — treat
    any row neither pass fills as a genuine AMT gap, not a mapping miss.
  - SerialNumber: IK07 genuinely has NO full serial number column — only "Serial
    prefix" (values like "F520", a family code, not a per-unit serial; verified this
    is a real, distinct gap, same shape as Rio Tinto's own "Serial Prefix" column on
    Comp Grid). Return `source_column: null` here; do not substitute "Serial prefix".
    The same code-level AMT cross-reference pass that recovers ComponentCode/
    ModifierCode via "Func Loc Key" also recovers the real Serial Number for these rows
    from rio-tinto_cross-reference.csv (which carries a full "Serial Number" column) —
    this call has no visibility into that and should not try to replicate it.
  - MeasTotCtr: "Total Machine\nHours" — the cumulative asset-level meter reading.
  - LifeToDateValue: "Component\nHours" is the component-specific cumulative reading
    (as opposed to "Total Machine\nHours", which is asset-level) — do not confuse the
    two; "LAO Date" is NOT a numeric reading despite its name and should not be picked
    here even though a weak deterministic pass may suggest it.
  - FrequencyValue: IK07 genuinely has NO interval/frequency column — it is a reading
    log (hours + dates + deltas), not a strategy-planning sheet. This is a confirmed,
    real gap in this customer's measurement-point export, not an extraction miss.
    Return null/low confidence rather than forcing "Difference" (a per-reading delta,
    not a planning interval) into this field.
