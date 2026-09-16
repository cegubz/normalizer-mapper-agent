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
    duplicate Func Loc columns actually holds the fullest/most specific code.
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
