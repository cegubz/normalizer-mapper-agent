"""Deterministic confidence scorer.

This is the reproducible arithmetic core recommended in the prompt: the numbers are
computed, not guessed by a model. It mirrors the rubric in schema_mapping_prompt.md.
"""
from __future__ import annotations
from difflib import SequenceMatcher


def _tokens(text: str) -> set:
    import re
    return set(re.sub(r"[^a-z0-9]", " ", str(text).lower()).split())


def band_for(confidence: float, bands: dict) -> str:
    """Map a confidence value to its band using the same thresholds as score_target.

    Shared so a non-deterministic confidence (e.g. the LLM's, when it is allowed to
    stand in for a low-confidence deterministic pick) is banded identically.
    """
    if confidence >= bands.get("auto_accept", 0.9):
        return "auto_accept"
    if confidence >= bands.get("accept_review", 0.7):
        return "accept_review"
    if confidence >= bands.get("low_review", 0.5):
        return "low_review"
    return "reject"


def name_score(header: str, aliases: list[str]) -> float:
    ht = " ".join(sorted(_tokens(header)))
    best = 0.0
    hset = _tokens(header)
    for a in aliases:
        at = " ".join(sorted(_tokens(a)))
        ratio = SequenceMatcher(None, ht, at).ratio()
        aset = _tokens(a)
        jacc = len(hset & aset) / max(1, len(hset | aset))
        best = max(best, 0.5 * ratio + 0.5 * jacc)
    return best


def score_target(
    fields: list[dict],
    column_profiles: list[dict],
    scoring: dict,
    join_overlap_fn=None,
) -> dict:
    """Score customer_file + join-key fields against columns; greedy one-to-one assign.

    Returns {canonical: {source_column, name_score, value_score, confidence, band}}.
    """
    nw = scoring.get("name_weight", 0.6)
    vw = scoring.get("value_weight", 0.4)
    jb = scoring.get("join_bonus", 0.15)
    cap = scoring.get("confidence_cap", 0.99)
    # Name-dominance gate: a column whose header barely matches (name_score below the
    # gate) cannot win on value evidence alone — its confidence is scaled down. This
    # stops a numeric column like "Counter reading" from stealing "TaskCounterCode".
    min_name_gate = scoring.get("min_name_gate", 0.55)
    gate_penalty = scoring.get("gate_penalty", 0.75)
    bands = scoring.get("bands", {"auto_accept": 0.9, "accept_review": 0.7, "low_review": 0.5})

    mappable = [f for f in fields if f.get("source_class") == "customer_file"]

    cells = []  # (confidence, field_idx, column, name_score, value_score)
    for fi, f in enumerate(mappable):
        dtype = f.get("dtype", "str")
        aliases = f.get("aliases", [])
        # A header containing any of these substrings is disqualified outright for this
        # field, regardless of score — for a lookalike-but-wrong column (e.g. "Serial
        # Prefix" for SerialNumber: a model-family code, not a per-unit serial) partial
        # token overlap with the real aliases can still clear the confidence bands, and
        # unlike a genuine low-confidence gap, writing it through would be actively
        # wrong, not just incomplete.
        excludes = [e.lower() for e in f.get("exclude_headers", [])]
        for prof in column_profiles:
            if excludes and any(e in str(prof["column"]).lower() for e in excludes):
                continue
            ns = name_score(prof["column"], aliases)
            vs = prof["evidence"].get(dtype, 0.5)
            conf = nw * ns + vw * vs
            if f.get("join_key") and join_overlap_fn is not None:
                conf += jb * join_overlap_fn(prof["column"])
            if ns < min_name_gate:
                conf *= gate_penalty
            cells.append((conf, fi, prof["column"], round(ns, 3), round(vs, 3)))

    cells.sort(key=lambda c: c[0], reverse=True)
    used_f, used_c, out = set(), set(), {}
    for conf, fi, col, ns, vs in cells:
        if fi in used_f or col in used_c:
            continue
        used_f.add(fi)
        used_c.add(col)
        c = round(min(conf, cap), 3)
        band = band_for(c, bands)
        out[mappable[fi]["canonical"]] = {
            "source_column": col,
            "name_score": ns,
            "value_score": vs,
            "confidence": c,
            "band": band,
        }
    # Fields that never got a column
    for f in mappable:
        out.setdefault(
            f["canonical"],
            {"source_column": None, "name_score": 0.0, "value_score": 0.0,
             "confidence": 0.0, "band": "reject"},
        )
    return out
