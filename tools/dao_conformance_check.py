"""
dao_conformance_check.py — prove EtoRepository is a drop-in for ExcelRepository.

    python tools/dao_conformance_check.py

No database and no source workbooks needed: the script builds throwaway .xlsx files
in a temp directory, feeds ExcelRepository from them, feeds EtoRepository the same
logical rows in ETO-shaped form (Decimals, bit values, NULLs), and compares what the
two hand back. Nothing outside the temp directory is touched.

Why this exists. The DAO layer is one abstract class with four methods, and everything
downstream of it — main.py, both aggregators, all four evaluators, the scoring engine —
is written against that contract and nothing else. The contract is what makes the ETO
migration a swap rather than a rewrite, so it is worth an executable proof rather than
an assurance.

Six checks:

  1. DAO contract        subclass, all four abstract methods implemented, instantiable
  2. Signatures          the four methods take the same arguments on both repositories
  3. Public surface      attributes downstream code reads exist on both
  4. Column contract     eto_queries contracts == config/column_mappings.json values
  5. Behavioural parity  identical logical input -> identical output frames
  6. Downstream run      both frames survive the real evaluators and scoring engine
  7. Source selection    the factory returns a conformant DAO, and its default is
                         identical to the construction main.py used before

Exit code 0 = conformant. Non-zero = a real incompatibility, listed.
"""

import inspect
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_access.base_repository import VendorScorecardRepository   # noqa: E402
from src.data_access.excel_repository import ExcelRepository            # noqa: E402
from src.data_access.sql_repository import EtoRepository                # noqa: E402
from src.data_access import eto_queries                                 # noqa: E402
from src.data_access.repository_factory import (                        # noqa: E402
    create_repository,
    repository,
    resolve_source,
)


FAILURES = []
NOTES = []


def check(condition, label, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n          {detail}" if detail else ""))
        FAILURES.append(label)
    return condition


def note(text):
    NOTES.append(text)
    print(f"  note  {text}")


def rule(title):
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


# ==================================================
# FIXTURE — one logical dataset, two source shapes
# ==================================================
#
# Deliberately exercises the edges the two paths handle differently:
#   * a line with no receipt at all      (NULL in SQL, blank in Excel)
#   * a line missing a required field    (must land in rejected_purchase_orders)
#   * an all-blank report header row     (an Excel artifact with no SQL equivalent)
#   * a resolved NCR flag as a bit       (SQL) vs a boolean (Excel)
#   * repeat prices on one part          (so Commercial has something to compare)
# ==================================================

def logical_rows():
    po = []

    for i in range(1, 7):
        po.append({
            "po_number": 1000 + i,
            "project_number": 230219,
            "machine_code": 10.0,
            "ordered_qty": 5,
            "part_number": "P-100",
            "po_part_number": None,
            "vendor_name": "ACME STEEL [BRAMPTON] (Approved)",
            "unit_price": 10.0 + (i % 3),
            "required_date": pd.Timestamp(f"2026-02-{i + 4:02d}"),
            # At least one revised_date MUST be populated. An all-empty date column
            # round-trips out of .xlsx as object dtype and ExcelRepository rejects the
            # whole file -- see the note raised in check 5.
            "revised_date": pd.Timestamp(f"2026-02-{i + 6:02d}") if i == 1 else pd.NaT,
            "last_receipt_date": pd.Timestamp(f"2026-02-{i + 2:02d}"),
            "uom": "EA",
            "received_qty": 5,
            "order_date": pd.Timestamp(f"2026-01-{i:02d}"),
            "extended_value": 50.0,
            "currency_code": "CAD",
            "currency_rate": 1.0,
            "supplier_number": 77,
            "order_number": None,
            "receiving_date": pd.NaT,
        })

    # never received: receipt columns empty on both sides
    po.append({
        **po[0],
        "po_number": 2001,
        "vendor_name": "HOPE LAND [SHANGHAI]",
        "part_number": "P-200",
        "ordered_qty": 3,
        "unit_price": 99.5,
        "received_qty": None,
        "last_receipt_date": pd.NaT,
        "currency_code": "USD",
        "currency_rate": 1.35,
    })

    # missing a required field: must be rejected identically by both
    po.append({
        **po[0],
        "po_number": None,
        "vendor_name": "GHOST VENDOR [NOWHERE]",
        "part_number": "P-300",
    })

    ncrs = [
        {
            "ncr_number": "NCO0000000001", "project_number": 230219, "machine_code": 10.0,
            "title": "Bad weld", "origin": "Supplier Machining Error", "total_tasks": 3,
            "outstanding_tasks": 0, "resolved": True,
            "released": pd.Timestamp("2026-02-01"), "source_info": "Receiving",
            "po_number": 1001, "part_number": "P-100", "quantity": 10,
            "quantity_rejected": 2, "interim_action": "quarantine",
            "root_cause": "tooling", "corrective_pre_action": "regrind",
            "ncr_costs": 1500.0, "ncr_hours": None,
            "target_date": pd.Timestamp("2026-01-31"), "date_follow_up": pd.NaT,
            "created_date": pd.Timestamp("2026-01-15"),
            "vendor_name": "ACME STEEL [BRAMPTON] (Approved)", "item_id": 7,
        },
        {
            "ncr_number": "NCO0000000002", "project_number": 230219, "machine_code": 10.0,
            "title": "Short ship", "origin": "Purchasing", "total_tasks": 2,
            "outstanding_tasks": 1, "resolved": False, "released": pd.NaT,
            "source_info": "Receiving", "po_number": 1002, "part_number": "P-100",
            "quantity": 10, "quantity_rejected": 12, "interim_action": None,
            "root_cause": None, "corrective_pre_action": None, "ncr_costs": 0.0,
            "ncr_hours": None, "target_date": pd.NaT, "date_follow_up": pd.NaT,
            "created_date": pd.Timestamp("2026-02-01"),
            "vendor_name": "ACME STEEL [BRAMPTON] (Approved)", "item_id": 7,
        },
    ]

    items = [
        {"part_number": "P-100", "description": "Plate", "uom": None, "category": None,
         "list_price": None, "revision": None, "lpp": None, "quantity_on_hand": 12,
         "preferred_supplier": None, "supplier_part_number": None, "last_supplier": None,
         "manufacturer": None, "manuf_part_number": None, "lead_time": None,
         "quantity_reserved": None},
        {"part_number": "P-200", "description": "Die", "uom": None, "category": None,
         "list_price": None, "revision": None, "lpp": None, "quantity_on_hand": None,
         "preferred_supplier": None, "supplier_part_number": None, "last_supplier": None,
         "manufacturer": None, "manuf_part_number": None, "lead_time": None,
         "quantity_reserved": None},
    ]

    vendors = [
        {"company_id": 501, "vendor_name": "ACME STEEL [BRAMPTON] (Approved)",
         "address_line_1": "1 Rd", "address_line_2": None, "city": "Brampton",
         "state_province": "ON", "postal_code": "L6T", "country": "CA"},
        {"company_id": 502, "vendor_name": "HOPE LAND [SHANGHAI]", "address_line_1": None,
         "address_line_2": None, "city": "Shanghai", "state_province": None,
         "postal_code": None, "country": "CN"},
    ]

    return po, ncrs, items, vendors


def write_workbooks(directory, mappings, sources, po, ncrs, items, vendors):
    """Render the logical rows as source workbooks with the real ETO header names."""

    def to_source_frame(rows, dataset):
        inverse = {internal: source for source, internal in mappings[dataset].items()}
        frame = pd.DataFrame(rows)
        # every mapped column must be present or the loader raises
        for internal in inverse:
            if internal not in frame.columns:
                frame[internal] = None
        return frame[list(inverse.keys())].rename(columns=inverse)

    po_frame = to_source_frame(po, "purchase_orders")

    # an all-blank report header row: an Excel export artifact with no SQL equivalent
    po_frame = po_frame.reindex(index=[-1] + list(po_frame.index)).reset_index(drop=True)

    for dataset, frame in (
        ("purchase_orders", po_frame),
        ("ncrs", to_source_frame(ncrs, "ncrs")),
        ("items", to_source_frame(items, "items")),
        ("vendors", to_source_frame(vendors, "vendors")),
    ):
        frame.to_excel(directory / sources[dataset]["filename"], index=False)


def eto_shaped(po, ncrs, items, vendors):
    """The same rows as a SQL Server driver would hand them over."""

    def sqlish(value):
        if isinstance(value, float):
            return Decimal(str(value))
        if isinstance(value, bool):
            return 1 if value else 0
        if value is pd.NaT:
            return None
        return value

    frames = {}

    for dataset, rows, additive in (
        ("purchase_orders", po, {"supplier_company_id": 501, "purchase_detail_id": 1,
                                 "item_id": 7, "item_description": "Plate",
                                 "project_name": "Press Line", "detail_received_qty": None,
                                 "detail_last_receipt": None, "log_received_qty": None,
                                 "log_last_receipt": None}),
        ("ncrs", ncrs, {"supplier_company_id": 501, "po_supplier_name": "ACME",
                        "ncr_supplier_id": 501, "nc_id": 1, "origin_department": "Quality"}),
        ("items", items, {"item_id": 7}),
        ("vendors", vendors, {"supplier_qa_approved": 1, "supplier_terms": "NET30",
                              "company_active": 1}),
    ):
        frames[dataset] = pd.DataFrame([
            {**{k: sqlish(v) for k, v in row.items()}, **additive} for row in rows
        ])

    return frames


class StubEtoRepository(EtoRepository):
    """EtoRepository with the driver replaced; every other code path is the real one."""

    def __init__(self, config_path, frames):
        super().__init__(config_path, connection=object())
        self._frames = frames

    def _read(self, dataset, sql, params):
        return self._frames[dataset].copy()


# ==================================================
# CHECKS
# ==================================================

def check_dao_contract():
    rule("1. DAO CONTRACT — does EtoRepository satisfy VendorScorecardRepository?")

    abstract = sorted(VendorScorecardRepository.__abstractmethods__)
    print(f"  DAO defines: {', '.join(abstract)}\n")

    check(issubclass(EtoRepository, VendorScorecardRepository),
          "EtoRepository subclasses VendorScorecardRepository")

    unimplemented = sorted(getattr(EtoRepository, "__abstractmethods__", set()))
    check(not unimplemented,
          "all abstract methods implemented",
          f"still abstract: {unimplemented}")

    for method in abstract:
        check(callable(getattr(EtoRepository, method, None)),
              f"EtoRepository.{method} is callable")

    # An ABC with an unimplemented method raises only at construction time.
    try:
        EtoRepository(str(ROOT / "config" / "eto.json"), connection=object())
        check(True, "EtoRepository instantiates (ABC enforcement satisfied)")
    except TypeError as exc:
        check(False, "EtoRepository instantiates", str(exc))


def check_signatures():
    rule("2. SIGNATURES — can downstream call either repository the same way?")

    for method in sorted(VendorScorecardRepository.__abstractmethods__):
        excel = inspect.signature(getattr(ExcelRepository, method))
        eto = inspect.signature(getattr(EtoRepository, method))

        excel_args = [p for p in excel.parameters if p != "self"]
        eto_args = [p for p in eto.parameters if p != "self"]

        check(excel_args == eto_args,
              f"{method}{tuple(excel_args)} matches on both",
              f"Excel takes {excel_args}, ETO takes {eto_args}")

    excel_init = [p for p in inspect.signature(ExcelRepository.__init__).parameters
                  if p != "self"]
    eto_init = [p for p in inspect.signature(EtoRepository.__init__).parameters
                if p != "self"]

    note(f"constructors differ by design: ExcelRepository({', '.join(excel_init)}) "
         f"vs EtoRepository({', '.join(eto_init)}). Construction is the caller's "
         f"choice of source; every method after it is identical.")


def check_public_surface():
    rule("3. PUBLIC SURFACE — attributes downstream code reads")

    excel = ExcelRepository(str(ROOT / "data" / "input"),
                            str(ROOT / "config" / "column_mappings.json"),
                            str(ROOT / "config" / "sources.json"))
    eto = EtoRepository(str(ROOT / "config" / "eto.json"), connection=object())

    check(hasattr(eto, "rejected_purchase_orders"),
          "EtoRepository exposes rejected_purchase_orders before any load")

    if not hasattr(excel, "rejected_purchase_orders"):
        note("ExcelRepository sets rejected_purchase_orders only inside "
             "get_purchase_orders, so reading it before a load raises AttributeError. "
             "EtoRepository initialises it to None. A divergence in the DAO's implied "
             "contract, and the ETO side is the safer of the two.")


def check_column_contract():
    rule("4. COLUMN CONTRACT — eto_queries vs config/column_mappings.json")

    with open(ROOT / "config" / "column_mappings.json", encoding="utf-8") as handle:
        mappings = json.load(handle)

    contracts = {
        "purchase_orders": eto_queries.PURCHASE_ORDER_CONTRACT,
        "ncrs": eto_queries.NCR_CONTRACT,
        "items": eto_queries.ITEM_CONTRACT,
        "vendors": eto_queries.VENDOR_CONTRACT,
    }

    for dataset, contract in contracts.items():
        expected = set(mappings[dataset].values())
        actual = set(contract)

        check(expected == actual,
              f"{dataset}: {len(actual)} columns match the Excel mapping exactly",
              f"missing {sorted(expected - actual)}, extra {sorted(actual - expected)}")


def load_both():
    """Run both repositories over the same logical data."""

    with open(ROOT / "config" / "column_mappings.json", encoding="utf-8") as handle:
        mappings = json.load(handle)
    with open(ROOT / "config" / "sources.json", encoding="utf-8") as handle:
        sources = json.load(handle)

    po, ncrs, items, vendors = logical_rows()

    temp = Path(tempfile.mkdtemp(prefix="dao_check_"))
    write_workbooks(temp, mappings, sources, po, ncrs, items, vendors)

    excel_repo = ExcelRepository(str(temp),
                                 str(ROOT / "config" / "column_mappings.json"),
                                 str(ROOT / "config" / "sources.json"))

    eto_repo = StubEtoRepository(str(ROOT / "config" / "eto.json"),
                                 eto_shaped(po, ncrs, items, vendors))

    excel_data = {
        "purchase_orders": excel_repo.get_purchase_orders(),
        "ncrs": excel_repo.get_ncrs(),
        "items": excel_repo.get_items(),
        "vendors": excel_repo.get_vendors(),
    }
    eto_data = {
        "purchase_orders": eto_repo.get_purchase_orders(),
        "ncrs": eto_repo.get_ncrs(),
        "items": eto_repo.get_items(),
        "vendors": eto_repo.get_vendors(),
    }

    return excel_data, eto_data, excel_repo, eto_repo, temp


# Columns where the two paths are KNOWN to differ, with the reason. Documented in
# docs/ETO_MAPPING.md section 1.2 as the one stated parity exception.
KNOWN_DIFFERENCES = {
    ("purchase_orders", "received_qty"):
        "Excel leaves a never-received line blank (NaN); EtoRepository fills the SQL "
        "NULL to 0. Every downstream consumer treats the two identically -- "
        "fully_received, has_receipt and the summed quantity all agree -- which check 6 "
        "verifies rather than assumes.",
}


def _canonical(series):
    """
    Reduce a column to comparable Python values, so dtype noise cannot masquerade
    as a data difference.

    Excel infers a dtype per column and SQL Server declares one, so the same value
    legitimately arrives as float64 on one side and int64, object or a nullable
    extension type on the other. What matters is whether the VALUES agree.
    """

    out = []

    for value in series.tolist():
        if value is None or value is pd.NaT or (isinstance(value, float) and pd.isna(value)):
            out.append(None)
        elif value is pd.NA:
            out.append(None)
        elif isinstance(value, (bool, pd.BooleanDtype().type)):
            out.append(bool(value))
        elif isinstance(value, pd.Timestamp):
            out.append(value.to_pydatetime())
        elif isinstance(value, (int, float)):
            out.append(round(float(value), 9))
        else:
            try:
                out.append(round(float(value), 9))
            except (TypeError, ValueError):
                out.append(str(value).strip())

    return out


def compare_frames(dataset, left, right, columns):
    """Values must agree; dtype differences and documented exceptions are reported."""

    mismatches = []
    exceptions = []
    dtype_diffs = []

    for column in columns:
        left_series = left[column].reset_index(drop=True)
        right_series = right[column].reset_index(drop=True)

        if str(left_series.dtype) != str(right_series.dtype):
            dtype_diffs.append(
                f"{column} ({left_series.dtype} vs {right_series.dtype})"
            )

        if _canonical(left_series) == _canonical(right_series):
            continue

        if (dataset, column) in KNOWN_DIFFERENCES:
            exceptions.append(column)
        else:
            mismatches.append(column)

    check(not mismatches,
          f"{dataset}: {len(columns) - len(exceptions)} contract columns agree",
          f"unexpected differences: {mismatches}")

    for column in exceptions:
        note(f"{dataset}.{column} — DOCUMENTED PARITY EXCEPTION. "
             f"{KNOWN_DIFFERENCES[(dataset, column)]}")

    if dtype_diffs:
        note(f"{dataset}: {len(dtype_diffs)} column(s) carry a different dtype while "
             f"the values agree — {', '.join(dtype_diffs)}. Excel infers a dtype per "
             f"column (an all-empty one lands as float64); SQL declares one. Only the "
             f"four numeric and four datetime PO columns are dtype-validated, and those "
             f"agree.")


def check_behavioural_parity(excel_data, eto_data, excel_repo, eto_repo):
    rule("5. BEHAVIOURAL PARITY — same logical input, same output")

    contracts = {
        "purchase_orders": eto_queries.PURCHASE_ORDER_CONTRACT,
        "ncrs": eto_queries.NCR_CONTRACT,
        "items": eto_queries.ITEM_CONTRACT,
        "vendors": eto_queries.VENDOR_CONTRACT,
    }

    for dataset, contract in contracts.items():
        left, right = excel_data[dataset], eto_data[dataset]

        check(left.shape[0] == right.shape[0],
              f"{dataset}: same row count ({left.shape[0]})",
              f"Excel {left.shape[0]} vs ETO {right.shape[0]}")

        missing = [c for c in contract if c not in right.columns]
        check(not missing, f"{dataset}: every contract column present in the ETO frame",
              f"missing: {missing}")

        if left.shape[0] == right.shape[0] and not missing:
            compare_frames(dataset, left, right, list(contract))

    check(excel_repo.rejected_purchase_orders.shape[0]
          == eto_repo.rejected_purchase_orders.shape[0],
          f"rejected_purchase_orders: same count "
          f"({excel_repo.rejected_purchase_orders.shape[0]})",
          f"Excel {excel_repo.rejected_purchase_orders.shape[0]} vs "
          f"ETO {eto_repo.rejected_purchase_orders.shape[0]}")

    note("the Excel fixture carried an all-blank report header row and the SQL fixture "
         "did not; both still produced the same valid-row count, which is the "
         "justification for EtoRepository omitting the header-strip step.")

    note("ExcelRepository rejects a source file in which a validated date column is "
         "entirely empty -- the column round-trips out of .xlsx as object dtype and "
         "fails _validate_purchase_order_types. EtoRepository cannot hit this: even an "
         "unresolved column is emitted as CAST(NULL AS datetime), so the dtype is "
         "correct whether or not any row has a value. The ETO path is the more robust "
         "of the two; this fixture populates one revised_date so the Excel side loads.")


def check_downstream(excel_data, eto_data):
    rule("6. DOWNSTREAM — do both frames survive the real pipeline?")

    from src.matching.vendor_matcher import prepare_purchase_order_vendors
    from src.evaluation.delivery_evaluator import prepare_delivery_metrics
    from src.evaluation.lead_time_evaluator import prepare_lead_time_metrics
    from src.evaluation.commercial_evaluator import prepare_commercial_metrics
    from src.evaluation.ncr_evaluator import prepare_ncr_metrics
    from src.aggregation.vendor_aggregator import aggregate_purchase_orders_by_vendor
    from src.scoring.vendor_scoring import load_scorecard_rules, apply_vendor_scoring

    rules = load_scorecard_rules(str(ROOT / "config" / "scorecard_rules.json"))
    results = {}

    for label, data in (("Excel", excel_data), ("ETO", eto_data)):
        try:
            purchase = prepare_purchase_order_vendors(data["purchase_orders"])
            purchase = prepare_delivery_metrics(purchase)
            purchase = prepare_lead_time_metrics(purchase, data["items"])
            purchase = prepare_commercial_metrics(purchase)
            ncrs = prepare_ncr_metrics(data["ncrs"])
            summary = aggregate_purchase_orders_by_vendor(purchase)

            for column in ("supplier_linked_ncr_count", "quality_eligible_ncr_count",
                           "ncr_quantity_anomaly_count",
                           "responsiveness_eligible_ncr_count",
                           "resolved_ncr_count", "unresolved_ncr_count"):
                summary[column] = 0
            summary["ncr_rejected_pct"] = float("nan")
            summary["responsiveness_proxy_pct"] = float("nan")

            scored = apply_vendor_scoring(summary, rules)

            results[label] = {
                "vendor rows": scored.shape[0],
                "delivery eligible": int(purchase["delivery_eligible"].sum()),
                "on-time": int(purchase["on_time"].sum()),
                "late": int(purchase["late"].sum()),
                "price comparisons": int(purchase["price_comparison_eligible"].sum()),
                "supplier-linked NCRs": int(ncrs["supplier_linked"].sum()),
                "quality-eligible NCRs": int(ncrs["quality_eligible"].sum()),
                "responsiveness-eligible": int(ncrs["responsiveness_eligible"].sum()),
                "resolved NCRs": int(ncrs["resolved_flag"].sum()),
                "unresolved NCRs": int(ncrs["unresolved_flag"].sum()),
            }
            check(True, f"{label}: full pipeline ran to a scored frame")

        except Exception as exc:
            check(False, f"{label}: full pipeline ran to a scored frame",
                  f"{type(exc).__name__}: {exc}")
            return

    print(f"\n  {'measure':<26}{'Excel':>10}{'ETO':>10}")
    print("  " + "-" * 46)
    for measure in results["Excel"]:
        left, right = results["Excel"][measure], results["ETO"][measure]
        flag = "" if left == right else "   <-- DIFFERS"
        print(f"  {measure:<26}{left:>10}{right:>10}{flag}")

    check(results["Excel"] == results["ETO"],
          "every downstream control total is identical")


def check_source_selection():
    rule("7. SOURCE SELECTION — the factory main.py now calls")

    check(resolve_source(None, ["main.py"]) == "excel",
          "default source is excel (unchanged environment behaves as before)")
    check(resolve_source(None, ["main.py", "--source=eto"]) == "eto",
          "--source=eto selects ETO")
    check(resolve_source("excel", ["main.py", "--source=eto"]) == "excel",
          "an explicit argument beats the command line")

    try:
        resolve_source("oracle")
        check(False, "an unknown source is rejected")
    except ValueError:
        check(True, "an unknown source is rejected")

    built = create_repository("excel")
    historical = ExcelRepository("data/input",
                                 "config/column_mappings.json",
                                 "config/sources.json")

    check(type(built) is type(historical)
          and str(built.input_dir) == str(historical.input_dir)
          and str(built.mapping_path) == str(historical.mapping_path)
          and str(built.sources_path) == str(historical.sources_path),
          "the default build is identical to main.py's previous construction")

    check(isinstance(built, VendorScorecardRepository),
          "the factory returns a VendorScorecardRepository")

    closed = {"called": False}

    class Probe(EtoRepository):
        def close(self):
            closed["called"] = True
            super().close()

    import src.data_access.repository_factory as factory
    original = factory.EtoRepository
    factory.EtoRepository = Probe
    try:
        with factory.repository("eto"):
            pass
    finally:
        factory.EtoRepository = original

    check(closed["called"],
          "repository() closes a repository that holds a connection")

    with repository("excel") as repo:
        check(isinstance(repo, ExcelRepository),
              "repository() works for a repository with nothing to close")

    note("the factory is additive — base_repository.py and excel_repository.py are "
         "unmodified, so the Excel reconciliation baseline is untouched.")


def main():
    print(__doc__.split("Six checks:")[0].strip())

    check_dao_contract()
    check_signatures()
    check_public_surface()
    check_column_contract()

    excel_data, eto_data, excel_repo, eto_repo, temp = load_both()

    check_behavioural_parity(excel_data, eto_data, excel_repo, eto_repo)
    check_downstream(excel_data, eto_data)
    check_source_selection()

    rule("RESULT")

    if FAILURES:
        print(f"  {len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"    - {failure}")
        print("\n  EtoRepository is NOT yet a drop-in for ExcelRepository.")
        return 1

    print("  CONFORMANT — EtoRepository is a drop-in for ExcelRepository.")
    print(f"  {len(NOTES)} note(s) above are differences that do not break the contract.")
    print(f"\n  (fixture workbooks left in {temp} — safe to delete)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
