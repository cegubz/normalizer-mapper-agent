====================================================================
KNOWN WORKBOOK CONTEXT — Westrac consumption workbooks with a "Billiton" sheet
(verify against THIS run's real data; column order/content can change between exports,
so treat this as a strong prior, not a substitute for actually checking
`columns`/`samples` below)
====================================================================
These workbooks typically have "PARTS" and "COMPONENTS" tabs that are large RAW
transactional extracts (WORK_ORDER, QUANTITY, MONTH_YEAR_LONG, FUNCTIONAL_LOCATION_
DESCRIPTION...) — NOT the source sheet, even though "PARTS" matches this customer's
configured sheet-name pattern. The real source is a much smaller sheet called
"Billiton": a clean, already field-per-column layout. There is no LAO/measurement-
points sheet in these workbooks — LAO is expected to stay unmapped for this shape.

Billiton columns verified: Branch, Site, Fleet, Task Counter, Model, Asset Short,
Equipment, Component Code, Modifier Code, Frequency, Life To Date, Strategy Date,
Primary Part Number, BHP Part No., Sales Status, PO Number, Comments, Serial Number,
ST Description, Strategy Usage, Customer, Task Type, plus several match/upload/date
utility columns (BULK, UPLOAD DATE, AMT Date, MATCH B/C/D to PARTS/COMPONENTS, RLEP,
Variance).

NOTE — this documented column list was carried over from before this project had a
real Billiton-shaped workbook to test against (none exists in test-data/; the one
Westrac-family file present, "01. MAIN - FORECAST...", is deliberately excluded, see
below). "Component Code" / "Modifier Code" being literal columns here (like Rio
Tinto's Comp Grid) is the working assumption, not independently re-verified. If this
run's real `columns`/`samples` disagree with anything below, trust the real data.

Fields verified against Billiton:
  - ModelCode: Model — values like "785C", "793F".
  - AssetName: Equipment — values like "DT3168 - APX01529" (unit ID + serial pair);
    "Asset Short" holds just the bare unit ID ("DT3168") as an alternative.
  - TaskCounterCode: Task Counter — values like "1 - 1", "MAJOR - Major Overhaul".
  - StrategyTaskDescription: ST Description — values like "1000.00.RB.0 ENGINE".
  - FrequencyValue: Frequency — numeric hour intervals (15000, 20000, 40000).
  - StrategyDate: Strategy Date — real datetimes. Note "AMT Date" looks similar but is
    an unrelated/unreliable near-duplicate column — do not confuse the two.
  - FunctionalLoc: Billiton genuinely has NO functional-location code column.
    Branch/Site/Fleet are grouping labels only (e.g. "BHP-Yandi-Trucks"), and "BULK"
    (despite containing dashes) holds asset-key concatenations, not true FLOC codes.
    This has repeatedly, correctly scored low (~0.40-0.60). Only override this if
    THIS run's actual samples show a real FLOC pattern — otherwise null/low
    confidence is the honest answer; do not force "BULK" or "Fleet" onto it.
  - NewStrategyDate: Billiton genuinely has no second date distinct from Strategy
    Date. "UPLOAD DATE" is a batch-upload timestamp (values cluster on month-end
    dates), not a planned-start date — do not pick it just because it is date-shaped.
    Treat this as a real gap unless this run's data shows otherwise.
  - ComponentCode / ModifierCode: map "Component Code" / "Modifier Code" directly if
    this run's real columns confirm they exist (see the NOTE above — unverified for
    this shape specifically). If they genuinely aren't present, return `source_column:
    null` rather than guessing — a code-in-code AMT cross-reference fallback
    (core/cross_reference.py's bhp_neo, Model + "Primary Part Number"/"BHP Part No."
    looked up against bhp_cross-reference.csv) fills blanks afterward from the lookup
    table, but — unlike the FMG and Rio Tinto join keys, which were verified against
    real workbook data — this one is best-effort against the cross-reference file's own
    structure only, since no real Billiton workbook exists in test-data/ to confirm the
    join key format or the "AMT" compound-string parse against.
  - SerialNumber: map "Serial Number" directly if this run's real columns confirm it
    exists (same unverified-for-this-shape caveat as above). bhp_cross-reference.csv has
    no serial-number column of its own, so unlike ComponentCode/ModifierCode there is no
    code-level fallback for this field here — if "Serial Number" genuinely isn't
    present, return `source_column: null`; it stays a genuine gap, not something the
    AMT cross-reference pass can recover.
