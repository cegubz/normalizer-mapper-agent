"""Smoke test — runs the deterministic path and checks the output contract.

Run:  USE_LLM=false python -m pytest tests/ -q
(or)  USE_LLM=false python tests/test_smoke.py <path-to-xlsx>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent import run_agent  # noqa: E402


def run(xlsx_path: str):
    resp = run_agent({
        "customer_id": "default",
        "input": {"path": xlsx_path},
        "output_dir": os.path.join(os.path.dirname(__file__), "_out_test"),
    })
    assert resp["status"] == "succeeded", resp
    rep = resp["mapping_report"]
    # sheets detected
    roles = {s["role"] for s in rep["matched_sheets"]}
    assert {"LTP", "MEASUREMENT_POINTS"} <= roles
    # outputs present
    for k in ("NEO", "LAO", "Normalized"):
        assert k in resp["outputs"]
    # every field carries a status; enrichment never invents a source column
    for target, rows in rep["mappings"].items():
        for m in rows:
            assert "status" in m
            if m["source_class"] == "enrichment":
                assert m["source_column"] is None
    print("OK:", rep["row_counts"], rep["column_confidence_summary"])
    return resp


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "CB_MM_LTP_AUGUST.xlsx"
    run(path)
