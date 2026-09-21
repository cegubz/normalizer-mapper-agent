"""Mapping engine — orchestrates the established workflow end to end.

Order (unchanged from the project):
  detect sheets -> profile columns -> deterministic score -> LLM refine (optional)
  -> normalize/quarantine rows -> build NEO/LAO -> AMT cross-reference enrichment
  -> per-row ConfidenceScore (core/row_confidence.py) -> business-rule exclusion
  (core/exclusions.py) -> both-blank ComponentCode/ModifierCode removal
  (core/exceptions.py's split_blank_pair) -> keep-latest-date deduplication
  (core/deduplication.py) -> post-build exception audit (core/exceptions.py's
  find_exceptions) -> assemble mapping_report.

Two entrypoints share that same order via _assemble_outputs(): run_mapping() resolves
frames from a multi-sheet workbook (sheet name -> role); run_mapping_from_reference_files()
resolves frames from standalone reference CSVs (filename -> role) instead — see
REFERENCE_FILES.md for the latter. Both hand _assemble_outputs() the same shape:
{role: frame-key} into a {frame-key: DataFrame} pool, plus the customer config.
"""
from __future__ import annotations
import os
import pandas as pd

from . import profiling, scorer, builders, normalizer, cross_reference
from . import exceptions as exceptions_mod
from . import exclusions as exclusions_mod
from . import deduplication
from . import row_confidence
from .llm_mapper import refine_mapping
from .settings import settings, detect_prompt_variant

# Content-rescue thresholds for sheet selection (see _content_score / run_mapping).
# A sheet whose name matches no configured pattern for a role can still be picked, but
# only if it plausibly fits (MIN) and clearly beats whatever name-matching found
# (MARGIN) — this avoids a coincidentally-similar, unrelated sheet stealing a role.
_RESCUE_MIN_SCORE = 0.60
_RESCUE_MARGIN = 0.10


def _content_score(frames: dict, sheet: str, fields: list[dict], scoring: dict) -> float:
    """Mean deterministic confidence this sheet's columns would get against `fields` —
    i.e. how well this sheet's content (not its name) fits a target's canonical fields.
    """
    df = frames[sheet]
    if df.empty:
        return -1.0
    profiles = profiling.profile_columns(df)
    det = scorer.score_target(fields, profiles, scoring)
    vals = [d["confidence"] for d in det.values()]
    return sum(vals) / len(vals) if vals else 0.0


def _pick_sheet(frames: dict, candidates: list[str], fields: list[dict], scoring: dict) -> str:
    """Tie-break multiple matching tabs by how well their columns actually score
    against the target's canonical fields (reusing the deterministic scorer) — NOT by
    row count. A large raw extract (e.g. a "PARTS" transaction log, or a sheet that
    happens to be literally named "LTP" but is raw SAP data) can dwarf a smaller,
    already-clean/pre-mapped sheet in row count while scoring far worse on content.
    """
    if len(candidates) == 1:
        return candidates[0]
    return max(candidates, key=lambda s: _content_score(frames, s, fields, scoring))


def _resolve_workbook_roles(workbook_path: str, cfg: dict) -> tuple[dict, dict, list, list]:
    """For ONE workbook: detect sheets per role via cfg['sheet_config'] and resolve
    naming ambiguity/content-rescue exactly as this project's run_mapping() always has
    — factored out so run_mapping_from_multiple_workbooks() (merging several workbooks)
    can reuse the identical per-workbook resolution instead of a parallel
    reimplementation that could quietly drift from it.

    Returns (frames, role_to_sheet, warnings, still_unmatched) — the full sheet pool and
    a role->sheet-name pointer into it (not pre-extracted DataFrames), so a single
    caller (run_mapping) can hand this straight to _assemble_outputs unchanged.
    """
    frames = profiling.read_workbook(workbook_path)
    sheet_match = profiling.match_sheets(list(frames.keys()), cfg["sheet_config"])
    role_target = {e["role"]: e["target"] for e in cfg["sheet_config"]}

    # Resolve role -> concrete sheet name. Two passes:
    #   1. Among name-pattern-matched candidates, pick the best-scoring one (as before).
    #   2. Content-rescue: scan every OTHER, not-yet-claimed sheet in the workbook in
    #      case the true source sheet's name matches no configured pattern at all (a
    #      sheet named after the customer, e.g., rather than "LTP"/"parts"/etc.) — only
    #      swap in a rescued sheet when it clearly, decisively fits better.
    role_to_sheet, warnings, claimed = {}, [], set()

    for role, info in sheet_match["matched"].items():
        target_fields = cfg["targets"].get(info["target"], {}).get("fields", [])
        scoring = cfg["scoring"]
        candidates = [s for s in info["candidates"] if s not in claimed] or info["candidates"]
        name_pick = _pick_sheet(frames, candidates, target_fields, scoring)
        name_score = _content_score(frames, name_pick, target_fields, scoring)

        rescue_pool = [s for s in frames if s not in claimed and s not in info["candidates"]]
        best_rescue, best_rescue_score = None, -1.0
        for s in rescue_pool:
            sc = _content_score(frames, s, target_fields, scoring)
            if sc > best_rescue_score:
                best_rescue, best_rescue_score = s, sc

        if (
            best_rescue is not None
            and best_rescue_score >= _RESCUE_MIN_SCORE
            and best_rescue_score >= name_score + _RESCUE_MARGIN
        ):
            chosen = best_rescue
            warnings.append(
                f"Role {role}: '{chosen}' (content score {best_rescue_score:.2f}) fits "
                f"better than name-matched {info['candidates']} (best {name_score:.2f}); "
                f"its name matches no configured pattern — consider adding one."
            )
        else:
            chosen = name_pick
            if len(info["candidates"]) > 1:
                warnings.append(
                    f"Role {role} matched {info['candidates']}; picked '{chosen}' by content score."
                )

        role_to_sheet[role] = chosen
        claimed.add(chosen)

    # Roles no configured pattern matched at all get one more chance: the best-scoring
    # unclaimed sheet in the workbook, if it plausibly fits.
    still_unmatched = []
    for role in sheet_match["unmatched_roles"]:
        target_fields = cfg["targets"].get(role_target.get(role), {}).get("fields", [])
        scoring = cfg["scoring"]
        pool = [s for s in frames if s not in claimed]
        best, best_score = None, -1.0
        for s in pool:
            sc = _content_score(frames, s, target_fields, scoring)
            if sc > best_score:
                best, best_score = s, sc
        if best is not None and best_score >= _RESCUE_MIN_SCORE:
            role_to_sheet[role] = best
            claimed.add(best)
            warnings.append(
                f"Role {role} matched no configured sheet-name pattern; content-score "
                f"rescued '{best}' ({best_score:.2f}) — consider adding a name pattern."
            )
        else:
            still_unmatched.append(role)

    return frames, role_to_sheet, warnings, still_unmatched


def run_mapping(workbook_path: str, cfg: dict) -> dict:
    prompt_variant = detect_prompt_variant(workbook_path)
    frames, role_to_sheet, warnings, still_unmatched = _resolve_workbook_roles(workbook_path, cfg)
    return _assemble_outputs(frames, role_to_sheet, cfg, prompt_variant, warnings, still_unmatched)


def run_mapping_from_multiple_workbooks(workbook_paths: list[str], cfg: dict) -> dict:
    """NEW workflow (the "true" workflow, per project instructions): merge SEVERAL
    customer workbooks — e.g. one per site, one per month, however the customer's
    export tool splits them — into ONE combined NEO/LAO, rather than treating each
    workbook as its own separate run. Expected to produce more rows than the
    single-workbook workflow, since it's effectively a union across all posted files.

    Each workbook is resolved independently via _resolve_workbook_roles() — its own
    sheet detection/name-rescue, exactly as run_mapping() does for a lone workbook, so
    a workbook with e.g. differently-named tabs than another still resolves correctly
    on its own terms. The SAME role's resolved DataFrame is then concatenated across
    every workbook that had one (a role only some workbooks carry is not an error —
    those rows just come from the workbooks that had it), and the shared pipeline
    (profile -> score -> LLM refine -> normalize -> build -> AMT cross-reference ->
    ConfidenceScore -> exclusion -> blank-pair removal -> dedup -> exception audit) in
    _assemble_outputs() runs exactly ONCE on the merged pool — every filtering/dropping
    rule from the single-workbook workflow applies identically here, unmodified, since
    they all live downstream of this function in the exact same code path.

    Every merged row carries a `_source_workbook` tag (the originating file's basename)
    for traceability — internal-only, never part of the canonical NEO/LAO schema (it's
    dropped the same way any other non-output_columns field is, in builders.build_target).
    """
    prompt_variant = None
    variant_sources: dict[str, list[str]] = {}
    role_frames_by_workbook: list[tuple[str, dict]] = []
    warnings: list[str] = []

    for path in workbook_paths:
        basename = os.path.basename(path)
        variant = detect_prompt_variant(path)
        if variant:
            variant_sources.setdefault(variant, []).append(basename)
        if prompt_variant is None:
            prompt_variant = variant

        frames, role_to_sheet, wb_warnings, wb_unmatched = _resolve_workbook_roles(path, cfg)
        role_frames_by_workbook.append(
            (basename, {role: frames[sheet] for role, sheet in role_to_sheet.items()})
        )
        warnings.extend(f"[{basename}] {w}" for w in wb_warnings)
        if wb_unmatched:
            warnings.append(f"[{basename}] role(s) {wb_unmatched} not matched in this workbook.")

    if len(variant_sources) > 1:
        warnings.append(
            f"Posted workbooks resolved to different customer/workbook-shape prompt "
            f"variants ({variant_sources}) — using {prompt_variant!r} (from the first "
            f"posted file) for all of them. Double-check these are meant to be merged "
            f"together, not separate customers."
        )

    all_roles = {entry["role"] for entry in cfg["sheet_config"]}
    frames, role_to_sheet = {}, {}
    for role in all_roles:
        parts = [
            df.assign(_source_workbook=basename)
            for basename, role_dfs in role_frames_by_workbook
            for r, df in role_dfs.items()
            if r == role
        ]
        if not parts:
            continue
        key = f"{role} (merged from {len(parts)}/{len(workbook_paths)} workbooks)"
        frames[key] = pd.concat(parts, ignore_index=True)
        role_to_sheet[role] = key

    still_unmatched = sorted(all_roles - set(role_to_sheet))
    if still_unmatched:
        warnings.append(
            f"Role(s) {still_unmatched} not matched in ANY of the {len(workbook_paths)} "
            f"posted workbooks — its target output is skipped this call."
        )

    return _assemble_outputs(frames, role_to_sheet, cfg, prompt_variant, warnings, still_unmatched)


def run_mapping_from_reference_files(file_paths: list[str], cfg: dict) -> dict:
    """Additional workflow: build NEO and/or LAO directly from standalone CSVs (e.g. an
    LTP export and/or a Measurement-Points export) instead of a single multi-sheet
    workbook. Each file is an independent primary source for its own target — LTP.csv
    alone produces NEO only, Measurement-Points.csv alone produces LAO only, and both
    together additionally get the cross-file join-key bonus (see the mp_role handling
    in _assemble_outputs). Call this with just the one file you have; there's no
    requirement to post both. A file's role — LTP -> NEO, MEASUREMENT_POINTS -> LAO —
    is auto-detected from its filename via sheet_config[].filename_patterns (see
    profiling.match_reference_files): the same role/target wiring already used by
    run_mapping(), just matched against filenames instead of Excel tab names. See
    REFERENCE_FILES.md.
    """
    basenames = {os.path.basename(p): p for p in file_paths}
    frames = {name: profiling.read_reference_csv(path) for name, path in basenames.items()}

    file_match = profiling.match_reference_files(list(frames.keys()), cfg["sheet_config"])
    role_to_sheet, warnings, claimed = {}, [], set()

    for role, info in file_match["matched"].items():
        candidates = [f for f in info["candidates"] if f not in claimed] or info["candidates"]
        if len(candidates) > 1:
            # More than one posted file matched this role's filename_patterns — tie-break
            # by content score, same as an ambiguous workbook sheet match.
            target_fields = cfg["targets"].get(info["target"], {}).get("fields", [])
            chosen = _pick_sheet(frames, candidates, target_fields, cfg["scoring"])
            warnings.append(
                f"Role {role} matched multiple reference files {candidates}; "
                f"picked '{chosen}' by content score."
            )
        else:
            chosen = candidates[0]
        role_to_sheet[role] = chosen
        claimed.add(chosen)

    still_unmatched = list(file_match["unmatched_roles"])
    if still_unmatched:
        # Expected and benign when you intentionally posted only file(s) for other
        # role(s) (e.g. just LTP.csv, to get NEO only) — that role's target is simply
        # skipped below (see _assemble_outputs). Only worth a closer look if you
        # *did* post a file for this role and it still didn't match: check
        # filename_patterns in the customer config against the actual filename.
        warnings.append(
            f"No file provided (or matched) for role(s) {still_unmatched} — "
            f"its target output is skipped this call."
        )

    # Reuse the workbook-variant prompt selection against whichever posted filename (if
    # any) happens to match a known customer/workbook-shape pattern (see settings.py).
    prompt_variant = next((v for p in file_paths if (v := detect_prompt_variant(p))), None)

    return _assemble_outputs(frames, role_to_sheet, cfg, prompt_variant, warnings, still_unmatched)


def _assemble_outputs(
    frames: dict,
    role_to_sheet: dict,
    cfg: dict,
    prompt_variant: str | None,
    warnings: list[str],
    unmatched_roles: list[str],
) -> dict:
    """Shared tail of both entrypoints: profile -> score -> LLM refine -> normalize ->
    build, then assemble the mapping_report. `role_to_sheet` maps a role (e.g. "LTP")
    to a key into `frames` — a workbook sheet name or a reference-file basename.
    """
    # Measurement-Points functional-location values for the LTP join-key bonus
    mp_role = cfg["normalization"].get("join_key_role", "MEASUREMENT_POINTS")
    mp_key_values = set()
    if mp_role in role_to_sheet:
        mp_df = frames[role_to_sheet[mp_role]]
        for col in mp_df.columns:
            if "functional" in str(col).lower() or "function location" in str(col).lower():
                mp_key_values = set(mp_df[col].dropna().astype(str))
                break

    report = {
        "matched_sheets": [
            {"role": r, "sheet_name": s, "rows": int(len(frames[s]))}
            for r, s in role_to_sheet.items()
        ],
        "unmatched_roles": unmatched_roles,
        "mappings": {},
        "column_confidence_summary": {},
        "row_counts": {},
        "warnings": warnings,
        "llm_used": settings.llm_configured(),
        "prompt_variant": prompt_variant or "main",
    }

    outputs = {}           # target -> DataFrame
    exceptions = {}        # target -> DataFrame (core/exceptions.py, post-build audit + blank-pair removal)
    rejected_frames = []   # Normalized rows across sheets
    excluded_frames = []   # ExcludedComponents rows across targets (core/exclusions.py)
    duplicate_frames = []  # DuplicateDates rows across targets (core/deduplication.py)

    for target, tcfg in cfg["targets"].items():
        role = tcfg["source_sheet_role"]
        if role not in role_to_sheet:
            report["mappings"][target] = []
            continue
        df = frames[role_to_sheet[role]]
        profiles = profiling.profile_columns(df)

        # join-overlap function for the join key
        def _overlap(colname, _df=df):
            vals = set(_df[colname].dropna().astype(str))
            if not vals or not mp_key_values:
                return 0.0
            return len(vals & mp_key_values) / len(vals)

        det = scorer.score_target(tcfg["fields"], profiles, cfg["scoring"], _overlap)

        # LLM refinement (alias reasoning + notes); deterministic numbers stand unless LLM
        # picks a *different valid* column, in which case we keep the deterministic score
        # for that column but record the LLM note.
        llm = refine_mapping(target, tcfg["fields"], profiles, det, prompt_variant=prompt_variant)

        resolved, rows = {}, []
        auto_accept_bar = cfg["scoring"].get("bands", {}).get("auto_accept", 0.9)
        for f in tcfg["fields"]:
            can = f["canonical"]
            sc = f.get("source_class")
            if sc == "customer_file":
                d = det.get(can, {})
                src = d.get("source_column")
                conf = d.get("confidence", 0.0)
                band = d.get("band", "reject")
                note = ""
                llm_d = llm.get(can)
                llm_col = llm_d.get("source_column") if llm_d else None

                if conf < auto_accept_bar and llm_d and llm_col:
                    # Deterministic ruling didn't clear auto_accept: skip it for this
                    # field and let the model's own column choice + calibrated
                    # confidence stand instead of the arithmetic score. See
                    # prompts/README.md ("Model-authoritative mapping below auto_accept").
                    src = llm_col
                    conf = round(float(llm_d.get("confidence", conf)), 3)
                    band = scorer.band_for(conf, cfg["scoring"].get("bands", {}))
                    note = (
                        f"Model-decided (deterministic {d.get('confidence', 0.0):.2f} "
                        f"< {auto_accept_bar:.2f}): {llm_d.get('notes', '')}"
                    )
                elif llm_d and llm_col and llm_col != src:
                    # Deterministic pick already cleared auto_accept; the LLM may still
                    # correct the column for review, but its computed score stands.
                    src = llm_col
                    note = f"LLM override: {llm_d.get('notes', '')}"
                elif llm_d:
                    note = llm_d.get("notes", "")

                if band == "reject" and src:
                    # "reject" means the winning column was still the best of a bad lot
                    # (the greedy scorer always assigns *something* if any column is
                    # unclaimed, with no confidence floor) — not a genuine candidate. A
                    # bare column name like "Measuring point" ending up as ComponentCode
                    # at 0.38 confidence is exactly the silently-wrong output this
                    # project's prompts explicitly rule out (see prompts/README.md); an
                    # honest blank is required here instead of writing it through.
                    note = (note + " | " if note else "") + (
                        f"Rejected low-confidence pick '{src}' ({conf:.2f} < low_review) "
                        f"— left blank rather than writing a non-candidate column."
                    )
                    src = None

                resolved[can] = src
                rows.append({
                    "canonical_field": can, "source_column": src, "source_class": sc,
                    "name_score": d.get("name_score", 0.0), "value_score": d.get("value_score", 0.0),
                    "confidence": conf, "band": band,
                    "status": "mapped" if src else "unmapped", "notes": note,
                })
            elif sc == "constant":
                rows.append({"canonical_field": can, "source_column": None, "source_class": sc,
                             "confidence": 1.0, "band": "auto_accept", "status": "constant", "notes": ""})
            elif sc == "derived":
                rows.append({"canonical_field": can, "source_column": None, "source_class": sc,
                             "confidence": None, "band": None, "status": "derived",
                             "notes": f"transform={f.get('transform')}"})
            else:  # enrichment
                rows.append({"canonical_field": can, "source_column": None, "source_class": sc,
                             "confidence": 0.0, "band": None, "status": "requires_enrichment",
                             "notes": f"join={f.get('join')}"})
        report["mappings"][target] = rows

        # Per-field "trust" for the row-level ConfidenceScore column (core/row_confidence.py)
        # — a different metric from the column-level numbers above. customer_file fields
        # use their own computed column-mapping confidence (even a low/rejected one — the
        # model did attempt it); constant/derived fields are trusted at 1.0 (not a guess).
        # Plain "enrichment" fields (BranchCode/SiteCode/...) are deliberately left OUT of
        # this dict for now — this customer shape's cross-reference pass may have no
        # mechanism to ever fill them, and a field the pipeline never attempts shouldn't
        # count against every row by the same fixed amount. See core/row_confidence.py's
        # module docstring. The AMT cross-reference block below adds one back in, at a
        # high trust, the moment it's actually filled for at least one row of this shape.
        field_confidence = {
            r["canonical_field"]: (r["confidence"] or 0.0)
            for r in rows
            if r["source_class"] == "customer_file"
        }
        field_confidence.update({
            r["canonical_field"]: 1.0 for r in rows if r["source_class"] in ("constant", "derived")
        })

        # The summary mean is scoped to fields actually written to <target>.csv
        # (output_columns) — not every customer_file field. Some customer_file fields
        # (e.g. FunctionalLoc, MeasTotCtr) exist only as internal join-key/derived-input
        # plumbing and are never in output_columns; including them would understate the
        # quality of what customers actually receive. They stay fully visible in
        # `mappings` and are reported separately here, never hidden.
        output_cols = set(tcfg.get("output_columns", []))
        customer_rows = [r for r in rows if r["source_class"] == "customer_file" and r["confidence"] is not None]
        deliverable_vals = [r["confidence"] for r in customer_rows if r["canonical_field"] in output_cols]
        support_rows = [r for r in customer_rows if r["canonical_field"] not in output_cols]

        report["column_confidence_summary"][f"{target}_mean"] = (
            round(sum(deliverable_vals) / len(deliverable_vals), 3) if deliverable_vals else None
        )
        if support_rows:
            report["column_confidence_summary"][f"{target}_support_fields"] = [
                {"canonical_field": r["canonical_field"], "confidence": r["confidence"]}
                for r in support_rows
            ]

        # normalize/quarantine
        key_field = cfg["normalization"]["join_key_canonical"]
        key_col = resolved.get(key_field)
        id_field = tcfg.get("identity_canonical")
        id_col = resolved.get(id_field, key_col)
        clean, rejected = normalizer.split_clean_rejected(
            df, role_to_sheet[role], key_col, id_col, cfg["normalization"]["reject_rules"]
        )
        if not rejected.empty:
            rejected_frames.append(rejected)

        outputs[target] = builders.build_target(clean, tcfg, resolved, cfg["constants"])

        # AMT cross-reference enrichment (equipment number / component code / modifier
        # code key for downstream Snowflake population) — fills blanks only, never
        # overwrites a customer_file-derived value. See core/cross_reference.py for the
        # per-workbook-shape join keys and what each has been verified against.
        filled = cross_reference.enrich(target, prompt_variant, outputs[target], clean, resolved)
        if filled:
            rows_by_field = {r["canonical_field"]: r for r in report["mappings"][target]}
            for canonical, count in filled.items():
                if not count:
                    continue
                row = rows_by_field.get(canonical)
                note = f"AMT cross-reference: filled {count} row(s) still blank after customer_file mapping."
                if row is not None:
                    row["notes"] = (row["notes"] + " | " + note).strip(" |") if row.get("notes") else note
                    row["status"] = f"{row['status']}+cross_reference" if row.get("status") else "cross_reference"
                # A join match is a deterministic exact-key lookup, not the fuzzy
                # column-alias guess customer_file confidence measures — e.g. FMG's
                # ComponentCode has no source column at all (rejected, near-0 confidence)
                # yet the AMT join fills it reliably. Trust wherever this field was
                # actually filled this way at least as much as any genuine customer_file
                # pick for it (see core/row_confidence.py's module docstring).
                field_confidence[canonical] = max(field_confidence.get(canonical, 0.0), 0.95)

        # Per-row confidence score (core/row_confidence.py) — appended as an EXTRA
        # trailing column, not part of the fixed output_columns schema. Computed here,
        # after cross-reference enrichment, so a cell the enrichment pass just filled
        # counts as present for this row; exceptions[target] below is a row-subset of
        # this same frame, so it inherits the column automatically, no separate
        # computation needed there.
        outputs[target]["ConfidenceScore"] = row_confidence.compute(
            outputs[target], tcfg.get("output_columns", []), field_confidence
        )

        # Business-rule row exclusion (core/exclusions.py) — a complete, well-mapped row
        # can still be removed from NEO/LAO entirely because of what it's ABOUT (its
        # StrategyTaskDescription matches an excluded category), not any data-quality
        # issue. Runs after ConfidenceScore so an excluded row keeps its score in
        # ExcludedComponents.csv; runs before row_counts/the exception audit below so
        # both reflect only what's actually still in NEO.csv/LAO.csv, and an excluded
        # row is never also flagged as an exception.
        excl_cfg = cfg.get("exclusions", {})
        kept, excluded_rows = exclusions_mod.split_excluded(
            outputs[target], excl_cfg.get("field"), excl_cfg.get("keywords", [])
        )
        if len(excluded_rows):
            excluded_rows["_source_target"] = target
            excluded_frames.append(excluded_rows)
            report.setdefault("excluded_by_keyword", {})[target] = (
                excluded_rows["_matched_keyword"].value_counts().to_dict()
            )
        outputs[target] = kept

        # Both-blank ComponentCode/ModifierCode removal (core/exceptions.py's
        # split_blank_pair) — unlike the audit below, this ACTUALLY removes the row
        # (no usable component identity at all), diverting it into the same
        # NEO_Exceptions.csv/LAO_Exceptions.csv the audit writes to. Runs before the
        # dedup step so a blank-component junk row never distorts a dedup group.
        kept, blank_pair_removed = exceptions_mod.split_blank_pair(
            outputs[target], "ComponentCode", "ModifierCode"
        )
        outputs[target] = kept

        # Keep-latest-date deduplication (core/deduplication.py) — a target with no
        # entry here (or no populated date field for this shape) is a no-op. Runs after
        # the blank-pair removal above so grouping/latest-date comparisons only ever
        # see rows with a real component identity.
        dedup_cfg = cfg.get("deduplication", {}).get(target, {})
        kept, duplicate_rows = deduplication.split_duplicates(
            outputs[target], dedup_cfg.get("group_by", []), dedup_cfg.get("date_field")
        )
        if len(duplicate_rows):
            duplicate_rows["_source_target"] = target
            duplicate_frames.append(duplicate_rows)
        outputs[target] = kept

        report["column_confidence_summary"][f"{target}_row_confidence_mean"] = (
            round(float(outputs[target]["ConfidenceScore"].mean()), 3)
            if len(outputs[target]) else None
        )

        report["row_counts"][target] = int(len(outputs[target]))
        report["row_counts"][f"{target}_ExcludedComponents"] = int(len(excluded_rows))
        report["row_counts"][f"{target}_DuplicateDates"] = int(len(duplicate_rows))

        # Post-build exception audit (core/exceptions.py) — runs on the FINAL rows,
        # after cross-reference enrichment above, so a cell it just filled is never
        # wrongly flagged as missing. Combined with blank_pair_removed above into one
        # file: audited rows stay in outputs[target] untouched (_removed_from_output
        # False); blank_pair_removed rows are already gone from it (True).
        audited = exceptions_mod.find_exceptions(outputs[target], cfg.get("exceptions", {}))
        exceptions[target] = (
            pd.concat([audited, blank_pair_removed], ignore_index=True)
            if len(blank_pair_removed) else audited
        )
        report["row_counts"][f"{target}_Exceptions"] = int(len(exceptions[target]))
        report["column_confidence_summary"][f"{target}_Exceptions_row_confidence_mean"] = (
            round(float(exceptions[target]["ConfidenceScore"].mean()), 3)
            if len(exceptions[target]) else None
        )
        if len(exceptions[target]):
            report.setdefault("exceptions_by_reason", {})[target] = (
                exceptions[target]["_exception_reason"].value_counts().to_dict()
            )

    # Surface a degraded cross-reference source (blob configured but unavailable for a
    # specific file, silently fell back to the bundled prompts/cross-references/ copy)
    # — worth an operator's attention; a plain "local" source (no blob configured at
    # all) is the normal zero-config case and isn't warning-worthy on its own, so it's
    # left out here. See core/cross_reference.py's xref_sources().
    for xref_name, source in cross_reference.xref_sources().items():
        if source == "local_fallback":
            report["warnings"].append(
                f"AMT cross-reference '{xref_name}': blob fetch failed (container "
                f"{settings.CROSS_REFERENCE_BLOB_CONTAINER!r}) — used the local "
                f"prompts/cross-references/ copy instead. Check the container/blob "
                f"name and storage auth if this is unexpected."
            )

    def _combine_or_empty(frames: list, extra_cols: list[str]) -> pd.DataFrame:
        """pd.concat(frames) when non-empty; otherwise a zero-row frame that still
        carries real column headers (a built target's own output schema + whichever
        tag columns this file adds) rather than a bare pd.DataFrame() — a CSV written
        from a truly columnless frame has no header row at all, which fails to parse
        back in for any consumer (pandas included) expecting one, even an empty file.
        """
        if frames:
            return pd.concat(frames, ignore_index=True)
        cols = (list(next(iter(outputs.values())).columns) if outputs else []) + extra_cols
        return pd.DataFrame(columns=cols)

    normalized = (
        pd.concat(rejected_frames, ignore_index=True) if rejected_frames else pd.DataFrame()
    )
    report["row_counts"]["Normalized"] = int(len(normalized))

    excluded_components = _combine_or_empty(excluded_frames, ["_matched_keyword", "_source_target"])
    report["row_counts"]["ExcludedComponents"] = int(len(excluded_components))

    duplicate_dates = _combine_or_empty(duplicate_frames, ["_source_target"])
    report["row_counts"]["DuplicateDates"] = int(len(duplicate_dates))

    return {
        "report": report,
        "outputs": outputs,
        "normalized": normalized,
        "exceptions": exceptions,
        "excluded_components": excluded_components,
        "duplicate_dates": duplicate_dates,
    }
