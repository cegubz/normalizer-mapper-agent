"""Local CLI runner — a drop-in alternative to the HTTP Function for testing.

Examples:
  python run_local.py --input /path/CB_MM_LTP_AUGUST.xlsx --customer default --out ./_out

  # TRUE/merge workflow — several Excel workbooks combined into ONE NEO/LAO
  # (see README.md §14); each is resolved independently, then the same role's rows
  # are concatenated across all of them before the shared pipeline runs once.
  python run_local.py --workbooks ./test-data/site1.xlsx ./test-data/site2.xlsx \\
      --customer default --out ./_out

  # Additional workflow — standalone reference CSVs instead of one workbook
  # (see REFERENCE_FILES.md); role is auto-detected per file from its filename.
  python run_local.py --reference-files ./test-data/LTP.csv ./test-data/Measurement-Points.csv \\
      --customer default --out ./_out
"""
import argparse
import json
from agent import run_agent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="Path to the customer .xlsx (single-workbook workflow)")
    ap.add_argument(
        "--workbooks",
        nargs="+",
        help="Several Excel workbooks to MERGE into one NEO/LAO (the TRUE workflow, "
        "see README.md §14) — alternative to --input. Each is resolved independently, "
        "then the same role's rows are concatenated across all of them.",
    )
    ap.add_argument(
        "--reference-files",
        nargs="+",
        help="Standalone reference CSVs (e.g. LTP.csv, Measurement-Points.csv) — "
        "alternative to --input, see REFERENCE_FILES.md. Role is auto-detected per "
        "file from its filename, so order doesn't matter.",
    )
    ap.add_argument("--customer", default="default", help="customer_id (config file stem)")
    ap.add_argument("--out", default=None, help="Local output dir")
    args = ap.parse_args()
    if not args.input and not args.workbooks and not args.reference_files:
        ap.error("one of --input, --workbooks, or --reference-files is required")

    request = {"customer_id": args.customer, "output_dir": args.out}
    if args.workbooks:
        request["workbooks"] = [{"path": p} for p in args.workbooks]
    elif args.reference_files:
        request["reference_files"] = [{"path": p} for p in args.reference_files]
    else:
        request["input"] = {"path": args.input}

    resp = run_agent(request)
    # Print the report (not the whole CSVs) for readability
    printable = {k: v for k, v in resp.items() if k != "outputs_inline"}
    print(json.dumps(printable, indent=2, default=str))


if __name__ == "__main__":
    main()
