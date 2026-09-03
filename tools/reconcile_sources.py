"""
reconcile_sources.py — prove the ETO queries reproduce the Excel extract before trusting them.

Parity is the whole migration strategy: until a SQL run and an Excel run agree, a changed
number is indistinguishable from a migration bug. This runs both repositories through the
same preparation stages and puts the control totals side by side, then diffs the scorecard
at vendor grain.

    python tools/reconcile_sources.py
    python tools/reconcile_sources.py --top 40        # widest vendor diffs to show
    python tools/reconcile_sources.py --csv out.csv   # full vendor diff to a file

Read-only against both sources. Writes nothing except an optional CSV you asked for.

Reading the output:

  Stage totals   Excel and ETO should match, or the difference should be explainable by
                 the scope settings in config/eto.json. A large PO-row gap means the
                 scope is wrong, not that the SQL is wrong -- see the D section of
                 eto_schema_probe.py.

  Vendor diff    Rows appearing in only one source are the interesting ones. They are
                 usually vendor-identity differences (a name that parses differently),
                 which is exactly the problem CompanyID is meant to remove.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data_access.excel_repository import ExcelRepository          # noqa: E402
from src.data_access.sql_repository import EtoRepository              # noqa: E402
from src.matching.vendor_matcher import prepare_purchase_order_vendors  # noqa: E402
from src.evaluation.delivery_evaluator import prepare_delivery_metrics  # noqa: E402
from src.evaluation.commercial_evaluator import prepare_commercial_metrics  # noqa: E402
from src.evaluation.lead_time_evaluator import prepare_lead_time_metrics    # noqa: E402
from src.evaluation.ncr_evaluator import prepare_ncr_metrics          # noqa: E402
from src.aggregation.vendor_aggregator import aggregate_purchase_orders_by_vendor  # noqa: E402


COMPARE_COLUMNS = [
    "po_transaction_count",
    "delivery_eligible_count",
    "on_time_count",
    "late_count",
    "lead_time_eligible_count",
    "price_comparison_count",
    "price_stable_count",
]


def load(repo, label):
    """Run a repository through the shared preparation stages."""

    print(f"\n=== {label}: loading ===")

    items = repo.get_items()
    purchase = repo.get_purchase_orders()
    ncrs = repo.get_ncrs()
    vendors = repo.get_vendors()

    purchase = prepare_purchase_order_vendors(purchase)
    purchase = prepare_delivery_metrics(purchase)
    purchase = prepare_lead_time_metrics(purchase, items)
    purchase = prepare_commercial_metrics(purchase)
    ncrs = prepare_ncr_metrics(ncrs)

    summary = aggregate_purchase_orders_by_vendor(purchase)

    return {
        "items": items,
        "purchase": purchase,
        "ncrs": ncrs,
        "vendors": vendors,
        "summary": summary,
    }


def stage_totals(data):
    purchase, ncrs = data["purchase"], data["ncrs"]

    return {
        "Item Master rows": data["items"].shape[0],
        "Items with lead time": int(data["items"]["lead_time"].notna().sum()),
        "Vendor Master rows": data["vendors"].shape[0],
        "PO rows (valid)": purchase.shape[0],
        "Distinct POs": int(purchase["po_number"].nunique()),
        "Vendor+Location rows": data["summary"].shape[0],
        "Delivery eligible": int(purchase["delivery_eligible"].sum()),
        "On-time": int(purchase["on_time"].sum()),
        "Late": int(purchase["late"].sum()),
        "Lead-time eligible": int(purchase["lead_time_eligible"].sum()),
        "Commercial base-eligible": int(purchase["commercial_base_eligible"].sum()),
        "Price comparisons": int(purchase["price_comparison_eligible"].sum()),
        "Price stable": int(purchase["price_stable"].sum()),
        "NCR rows": ncrs.shape[0],
        "Supplier-linked NCRs": int(ncrs["supplier_linked"].sum()),
        "Quality-eligible NCRs": int(ncrs["quality_eligible"].sum()),
        "Responsiveness-eligible NCRs": int(ncrs["responsiveness_eligible"].sum()),
        "Resolved NCRs": int(ncrs["resolved_flag"].sum()),
    }


def print_stage_comparison(excel_totals, eto_totals):
    print("\n" + "=" * 78)
    print("STAGE TOTALS")
    print("=" * 78)
    print(f"{'measure':<32}{'Excel':>12}{'ETO':>12}{'diff':>12}  ")
    print("-" * 78)

    for measure in excel_totals:
        left = excel_totals[measure]
        right = eto_totals.get(measure, 0)
        diff = right - left
        flag = "" if diff == 0 else "  <-- differs"
        print(f"{measure:<32}{left:>12,}{right:>12,}{diff:>+12,}{flag}")


def vendor_diff(excel_summary, eto_summary):
    keys = ["vendor_match_name", "vendor_match_city"]

    left = excel_summary[keys + COMPARE_COLUMNS].copy()
    right = eto_summary[keys + COMPARE_COLUMNS].copy()

    merged = left.merge(
        right,
        on=keys,
        how="outer",
        suffixes=("_excel", "_eto"),
        indicator=True,
    )

    for column in COMPARE_COLUMNS:
        merged[f"{column}_diff"] = (
            merged[f"{column}_eto"].fillna(0) - merged[f"{column}_excel"].fillna(0)
        )

    merged["abs_diff"] = merged[
        [f"{column}_diff" for column in COMPARE_COLUMNS]
    ].abs().sum(axis=1)

    return merged.sort_values("abs_diff", ascending=False)


def print_vendor_diff(merged, top):
    only_excel = int((merged["_merge"] == "left_only").sum())
    only_eto = int((merged["_merge"] == "right_only").sum())
    both = int((merged["_merge"] == "both").sum())
    identical = int(((merged["_merge"] == "both") & (merged["abs_diff"] == 0)).sum())

    print("\n" + "=" * 78)
    print("VENDOR + LOCATION RECONCILIATION")
    print("=" * 78)
    print(f"  In both sources          : {both:,}  ({identical:,} identical)")
    print(f"  Only in the Excel extract: {only_excel:,}")
    print(f"  Only in ETO              : {only_eto:,}")

    interesting = merged[
        (merged["abs_diff"] > 0) | (merged["_merge"] != "both")
    ].head(top)

    if interesting.empty:
        print("\n  No differences. The ETO queries reproduce the Excel extract exactly.")
        return

    # One-sided rows carry NaN on the missing side, and they sort to the top because
    # their abs_diff is largest -- so this must be NaN-safe or it crashes on exactly
    # the rows the tool exists to show.
    def whole(value):
        return int(value) if pd.notna(value) else 0

    print(f"\n  Widest {len(interesting)} differences:\n")
    print(f"    {'vendor':<34}{'location':<16}{'where':<12}{'PO x/e':>12}{'OTD x/e':>12}")
    print("    " + "-" * 84)

    for _, row in interesting.iterrows():
        where = {
            "left_only": "Excel only",
            "right_only": "ETO only",
            "both": "both",
        }[row["_merge"]]

        name = str(row["vendor_match_name"])[:33]
        city = str(row["vendor_match_city"])[:15]

        po_pair = (
            f"{whole(row['po_transaction_count_excel'])}/"
            f"{whole(row['po_transaction_count_eto'])}"
        )
        otd_pair = (
            f"{whole(row['on_time_count_excel'])}/"
            f"{whole(row['on_time_count_eto'])}"
        )

        print(f"    {name:<34}{city:<16}{where:<12}{po_pair:>12}{otd_pair:>12}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=25,
                        help="how many vendor differences to print (default 25)")
    parser.add_argument("--csv", default=None,
                        help="write the full vendor diff to this CSV")
    parser.add_argument("--eto-config", default="config/eto.json")
    args = parser.parse_args()

    excel_repo = ExcelRepository(
        "data/input", "config/column_mappings.json", "config/sources.json"
    )

    eto_repo = EtoRepository(args.eto_config)

    print(__doc__.split("Reading the output:")[0])

    eto_repo.check_ready(strict=False)

    blocking = eto_repo.blocking_gaps()
    if blocking:
        print("\n*** WARNING: load-bearing columns are still unresolved in config/eto.json:")
        for dataset, columns in blocking.items():
            print(f"      {dataset}: {', '.join(columns)}")
        print("    The affected components will score nothing on the ETO side, so the")
        print("    reconciliation below will show a false regression. Run the probe first.\n")

    excel_data = load(excel_repo, "Excel")

    try:
        eto_data = load(eto_repo, "ETO")
    finally:
        eto_repo.close()

    print_stage_comparison(stage_totals(excel_data), stage_totals(eto_data))

    merged = vendor_diff(excel_data["summary"], eto_data["summary"])
    print_vendor_diff(merged, args.top)

    if args.csv:
        merged.to_csv(args.csv, index=False)
        print(f"\n  Full vendor diff written to {args.csv}")

    print()


if __name__ == "__main__":
    main()
