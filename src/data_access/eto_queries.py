"""
eto_queries.py — SQL against the Total ETO database for the Vendor Scorecard.

Design rules this module obeys:

1.  **Views, not base tables.** Governance rule from REPORTING_SCHEMA_FRAMEWORK.md:
    production reporting reads ETO's views so the vendor's join logic (supplier name,
    item description, currency) stays in one place. Base tables are used only where no
    view is known to exist (tblCompany, tblSupplier, tblEngItemMaster, tblProjects).

2.  **Read-only.** Every statement here is a SELECT. Nothing writes to ETO, ever.

3.  **Column contract parity.** Each query returns exactly the internal column names the
    Excel pipeline already uses (config/column_mappings.json values), so `main.py` and
    every metric module run unchanged. Extra "additive" columns are appended; nothing
    downstream reads them yet.

4.  **Columns are configuration, not code.** Every projected column comes from
    config/eto.json. A column whose real name differs is fixed in JSON, not here. A
    column set to null emits a typed NULL so the contract holds while it is unresolved.

5.  **No string interpolation of values.** Scope values bind as pyodbc parameters.
    Only identifiers (object names, column expressions) come from config, and those are
    validated against an identifier pattern before they reach a statement.

Row-multiplication safety: every join added here is many-to-one or one-to-one on the
driver's grain, so no query can inflate the row count of its driver table.
  * PO detail -> header            N:1 on PurchaseOrderID
  * PO detail -> receiver summed   1:1 on PurchaseDetailID
  * PO detail -> item master       N:1 on ItemID (declared FK)
  * PO detail -> projects          N:1 on ProjectID
  * NCR -> list / costs / origin   1:1 / N:1
  * NCR -> PO header               N:1 on PurchaseOrderID (LEFT: 70% NULL)
  * Item master -> inventory       1:1 against a pre-grouped subquery
"""

import json
import re
from pathlib import Path


# ==================================================
# CONTRACTS
# ==================================================
#
# The column names each dataset MUST return for the existing pipeline to work.
# These mirror the values in config/column_mappings.json.
# ==================================================

PURCHASE_ORDER_CONTRACT = (
    "po_number", "project_number", "machine_code", "ordered_qty", "part_number",
    "po_part_number", "vendor_name", "unit_price", "required_date", "revised_date",
    "last_receipt_date", "uom", "received_qty", "order_date", "extended_value",
    "currency_code", "currency_rate", "supplier_number", "order_number", "receiving_date",
)

NCR_CONTRACT = (
    "ncr_number", "project_number", "machine_code", "title", "origin", "total_tasks",
    "outstanding_tasks", "resolved", "released", "source_info", "po_number",
    "part_number", "quantity", "quantity_rejected", "interim_action", "root_cause",
    "corrective_pre_action", "ncr_costs", "ncr_hours", "target_date", "date_follow_up",
    "created_date", "vendor_name", "item_id",
)

ITEM_CONTRACT = (
    "part_number", "description", "uom", "category", "list_price", "revision", "lpp",
    "quantity_on_hand", "preferred_supplier", "supplier_part_number", "last_supplier",
    "manufacturer", "manuf_part_number", "lead_time", "quantity_reserved",
)

VENDOR_CONTRACT = (
    "company_id", "vendor_name", "address_line_1", "address_line_2", "city",
    "state_province", "postal_code", "country",
)


# Columns whose SQL type must be pinned when the expression is NULL, so pandas
# still produces a usable dtype and the PO type validation passes.
_NULL_TYPES = {
    "ordered_qty": "decimal(18,6)",
    "received_qty": "decimal(18,6)",
    "unit_price": "decimal(18,6)",
    "extended_value": "decimal(18,6)",
    "currency_rate": "decimal(18,6)",
    "quantity": "decimal(18,6)",
    "quantity_rejected": "decimal(18,6)",
    "ncr_costs": "decimal(18,6)",
    "ncr_hours": "decimal(18,6)",
    "lead_time": "decimal(18,6)",
    "list_price": "decimal(18,6)",
    "lpp": "decimal(18,6)",
    "quantity_on_hand": "decimal(18,6)",
    "quantity_reserved": "decimal(18,6)",
    "total_tasks": "int",
    "outstanding_tasks": "int",
    "order_date": "datetime",
    "required_date": "datetime",
    "revised_date": "datetime",
    "last_receipt_date": "datetime",
    "receiving_date": "datetime",
    "created_date": "datetime",
    "released": "datetime",
    "target_date": "datetime",
    "date_follow_up": "datetime",
}
_DEFAULT_NULL_TYPE = "nvarchar(255)"


# ==================================================
# CONFIG LOADING AND VALIDATION
# ==================================================

# An identifier expression may contain only word characters, dots, brackets and
# whitespace. This is what keeps a mistyped config out of the statement text.
_SAFE_EXPR = re.compile(r"^[A-Za-z0-9_.\[\]]+$")
_SAFE_OBJECT = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")


def load_eto_config(config_path="config/eto.json"):
    """Load and validate the ETO source configuration."""

    with open(Path(config_path), "r", encoding="utf-8") as handle:
        cfg = json.load(handle)

    for key in ("connection", "scope", "options", "sources", "columns"):
        if key not in cfg:
            raise ValueError(f"config/eto.json is missing the '{key}' section")

    for name, obj in cfg["sources"].items():
        if not _SAFE_OBJECT.match(str(obj)):
            raise ValueError(
                f"sources.{name} = {obj!r} is not a valid 'schema.object' name"
            )

    for dataset, colmap in cfg["columns"].items():
        for alias, expr in colmap.items():
            if alias.startswith("_") or expr is None:
                continue
            if not _SAFE_EXPR.match(str(expr)):
                raise ValueError(
                    f"columns.{dataset}.{alias} = {expr!r} is not a bare column "
                    f"reference. Only 'alias.Column' expressions are allowed."
                )

    options = cfg["options"]
    allowed = options.get("_allowed", {})
    for key, choices in allowed.items():
        if options.get(key) not in choices:
            raise ValueError(
                f"options.{key} = {options.get(key)!r}; expected one of {choices}"
            )

    return cfg


def _columns(cfg, dataset):
    return {
        alias: expr
        for alias, expr in cfg["columns"][dataset].items()
        if not alias.startswith("_")
    }


def _select_list(colmap, contract, additive=()):
    """Build the SELECT list, emitting a typed NULL for unresolved columns."""

    parts = []

    for alias in tuple(contract) + tuple(additive):
        expr = colmap.get(alias)

        if expr is None:
            sql_type = _NULL_TYPES.get(alias, _DEFAULT_NULL_TYPE)
            parts.append(f"CAST(NULL AS {sql_type}) AS {alias}")
        else:
            parts.append(f"{expr} AS {alias}")

    return ",\n           ".join(parts)


def _additive(cfg, dataset):
    """Additive columns = everything in the map that is not part of the contract."""

    contracts = {
        "purchase_orders": PURCHASE_ORDER_CONTRACT,
        "ncrs": NCR_CONTRACT,
        "items": ITEM_CONTRACT,
        "vendors": VENDOR_CONTRACT,
    }
    contract = set(contracts[dataset])

    return tuple(
        alias for alias in _columns(cfg, dataset) if alias not in contract
    )


def _project_filter(alias, project_ids, params):
    """Optional project scope as bound parameters, never interpolated values."""

    if not project_ids:
        return ""

    placeholders = ", ".join("?" for _ in project_ids)
    params.extend(int(pid) for pid in project_ids)

    return f"\n      AND {alias}.ProjectID IN ({placeholders})"


def unresolved_columns(cfg, dataset):
    """Contract columns still set to null — what the probe has to answer."""

    contracts = {
        "purchase_orders": PURCHASE_ORDER_CONTRACT,
        "ncrs": NCR_CONTRACT,
        "items": ITEM_CONTRACT,
        "vendors": VENDOR_CONTRACT,
    }
    colmap = _columns(cfg, dataset)

    return tuple(
        alias for alias in contracts[dataset] if colmap.get(alias) is None
    )


# ==================================================
# PURCHASE ORDERS
# ==================================================

def build_purchase_order_sql(cfg):
    """
    PO lines at the same grain the Excel extract used: one row per PO detail line.

    Receipt figures come from whichever source options.received_qty_source and
    options.last_receipt_source select. The receiver log is ETO's own basis --
    dbo.urpPurchasingLateVendors uses vwReceiverLogSummed.MaxOfDate and
    SumOfQtyReceived, and a live comparison matched it 340/340 on project 230219.
    The PO detail's own Received / LastReceivedDate are kept as additive columns so
    the two can be reconciled rather than argued about.
    """

    src = cfg["sources"]
    scope = cfg["scope"]
    opts = cfg["options"]
    colmap = _columns(cfg, "purchase_orders")

    receipt_qty = (
        "rls.SumOfQtyReceived" if opts["received_qty_source"] == "receiver_log"
        else "pod.Received"
    )
    receipt_date = (
        "rls.MaxOfDate" if opts["last_receipt_source"] == "receiver_log"
        else "pod.LastReceivedDate"
    )

    colmap = dict(colmap)
    colmap["received_qty"] = receipt_qty
    colmap["last_receipt_date"] = receipt_date

    select_list = _select_list(
        colmap,
        PURCHASE_ORDER_CONTRACT,
        _additive(cfg, "purchase_orders"),
    )

    params = []
    where = ["1 = 1"]

    if scope.get("active_only"):
        where.append("poh.PurchaseActive = 1")

    if scope.get("exclude_archived_lines"):
        where.append("ISNULL(pod.Archived, 0) = 0")

    if scope.get("issued_only"):
        # A PO is issued to a vendor when it has been printed or emailed. Verified
        # 2026-08-03: there is no PO status lookup; these two bits are the signal.
        where.append("(poh.PurchasePrinted = 1 OR poh.PurchaseEmailed = 1)")

    if scope.get("po_date_from"):
        where.append("poh.PurchaseDate >= ?")
        params.append(scope["po_date_from"])

    if scope.get("po_date_to"):
        where.append("poh.PurchaseDate <= ?")
        params.append(scope["po_date_to"])

    where_sql = "\n      AND ".join(where)
    project_sql = _project_filter("pod", scope.get("project_ids"), params)

    sql = f"""
    SELECT {select_list}
    FROM {src['po_detail']} AS pod
    INNER JOIN {src['po_header']} AS poh
            ON poh.PurchaseOrderID = pod.PurchaseOrderID
    LEFT JOIN {src['receiver_summed']} AS rls
            ON rls.PurchaseDetailID = pod.PurchaseDetailID
    LEFT JOIN {src['item_master']} AS im
            ON im.ItemID = pod.ItemID
    LEFT JOIN {src['projects']} AS pj
            ON pj.ProjectID = pod.ProjectID
    WHERE {where_sql}{project_sql}
    """

    return sql, params


# ==================================================
# NON-CONFORMANCES
# ==================================================

def build_ncr_sql(cfg):
    """
    NCRs at one row per NonConformanceID.

    Two supplier paths are returned deliberately:

      vendor_name         nc.Supplier          the decorated display name the Excel
                                               pipeline parses today
      supplier_company_id poh.PurchaseSupplierID  the exact key, reached through the
                                               NCR's PurchaseOrderID

    The PO join MUST be LEFT: PurchaseOrderID is NULL on ~70% of NCRs (1,298 of 1,847
    verified 2026-07-04). An INNER join here would silently discard most of the NCR
    population.
    """

    src = cfg["sources"]
    scope = cfg["scope"]
    colmap = _columns(cfg, "ncrs")

    select_list = _select_list(colmap, NCR_CONTRACT, _additive(cfg, "ncrs"))

    params = []
    where = ["1 = 1"]

    if scope.get("ncr_active_only"):
        where.append("nc.SActive = 1")

    where_sql = "\n      AND ".join(where)
    project_sql = _project_filter("nc", scope.get("project_ids"), params)

    sql = f"""
    SELECT {select_list}
    FROM {src['ncr']} AS nc
    LEFT JOIN {src['ncr_list']} AS ncl
           ON ncl.NonConformanceID = nc.NonConformanceID
    LEFT JOIN {src['ncr_costs']} AS ncc
           ON ncc.NonConformanceID = nc.NonConformanceID
    LEFT JOIN {src['ncr_origin']} AS org
           ON org.NonConformanceOriginID = nc.NonConformanceOriginID
    LEFT JOIN {src['po_header']} AS poh
           ON poh.PurchaseOrderID = nc.PurchaseOrderID
    WHERE {where_sql}{project_sql}
    """

    return sql, params


# ==================================================
# ITEM MASTER
# ==================================================

def build_item_sql(cfg):
    """
    Item master, one row per item.

    On-hand quantity is pre-grouped before the join: ETO inventory is a shared pool
    with one row per item x location (verified 2026-08-03), so joining vwInventory
    directly would multiply item rows by their location count.

    Note on lead_time: im.EstimatedLeadTime is the benchmark the Lead-Time component
    needs, and it is EMPTY on every item (verified 2026-07-25). Migrating to ETO does
    not fix D-04 -- it confirms the benchmark does not exist in this source.
    """

    src = cfg["sources"]
    colmap = _columns(cfg, "items")

    select_list = _select_list(colmap, ITEM_CONTRACT, _additive(cfg, "items"))

    sql = f"""
    SELECT {select_list}
    FROM {src['item_master']} AS im
    LEFT JOIN (
             SELECT ItemID, SUM(QtyOnHand) AS QtyOnHand
             FROM {src['inventory']}
             GROUP BY ItemID
         ) AS inv
           ON inv.ItemID = im.ItemID
    """

    return sql, []


# ==================================================
# VENDOR MASTER
# ==================================================

def build_vendor_sql(cfg):
    """
    Vendor master from tblCompany.

    tblCompany is dual-purpose -- the same CName column serves customers (via
    tblProjects.CompanyID) and suppliers (via tblPurchaseOrderHeader.PurchaseSupplierID).
    A scope filter is therefore mandatory; without one this returns customers too.

      supplier_table    has a row in tblSupplier -- ETO's own definition of "is a supplier"
      active_suppliers  as above, and tblCompany.CActive = 1
      purchased_from    appears as a supplier on at least one PO
      all_companies     no filter -- diagnostic only, do not use for scoring

    The Excel extract held 1,803 rows against ~2,052 companies. Which scope reproduces
    1,803 is a reconciliation question, not an assumption; probe section G counts all four.

    tblSupplier is LEFT JOINed in every scope (the filter lives in the WHERE clause) so
    its attribute columns are addressable whichever scope is selected.
    """

    src = cfg["sources"]
    scope_mode = cfg["options"]["vendor_scope"]
    colmap = _columns(cfg, "vendors")

    select_list = _select_list(colmap, VENDOR_CONTRACT, _additive(cfg, "vendors"))

    predicates = {
        "supplier_table": "sup.CompanyID IS NOT NULL",
        "active_suppliers": "sup.CompanyID IS NOT NULL AND co.CActive = 1",
        "purchased_from": (
            f"EXISTS (SELECT 1 FROM {src['po_header']} AS h"
            f" WHERE h.PurchaseSupplierID = co.CompanyID)"
        ),
        "all_companies": "1 = 1",
    }

    sql = f"""
    SELECT {select_list}
    FROM {src['company']} AS co
    LEFT JOIN {src['supplier']} AS sup
           ON sup.CompanyID = co.CompanyID
    WHERE {predicates[scope_mode]}
    """

    return sql, []


# ==================================================
# RECONCILIATION HELPERS
# ==================================================

def build_scope_census_sql(cfg):
    """
    Row counts for each candidate PO scope, in one round trip.

    The Excel prototype scored 23,344 PO lines out of ~159,199. This is how that
    number is explained rather than guessed at.
    """

    src = cfg["sources"]

    return f"""
    SELECT 'all_lines' AS scope, COUNT(*) AS lines
    FROM {src['po_detail']} AS pod
    UNION ALL
    SELECT 'active_header', COUNT(*)
    FROM {src['po_detail']} AS pod
    INNER JOIN {src['po_header']} AS poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
    WHERE poh.PurchaseActive = 1
    UNION ALL
    SELECT 'active_not_archived', COUNT(*)
    FROM {src['po_detail']} AS pod
    INNER JOIN {src['po_header']} AS poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
    WHERE poh.PurchaseActive = 1 AND ISNULL(pod.Archived, 0) = 0
    UNION ALL
    SELECT 'active_not_archived_issued', COUNT(*)
    FROM {src['po_detail']} AS pod
    INNER JOIN {src['po_header']} AS poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
    WHERE poh.PurchaseActive = 1 AND ISNULL(pod.Archived, 0) = 0
      AND (poh.PurchasePrinted = 1 OR poh.PurchaseEmailed = 1)
    UNION ALL
    SELECT 'issued_with_receipt', COUNT(*)
    FROM {src['po_detail']} AS pod
    INNER JOIN {src['po_header']} AS poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
    LEFT JOIN {src['receiver_summed']} AS rls ON rls.PurchaseDetailID = pod.PurchaseDetailID
    WHERE poh.PurchaseActive = 1 AND ISNULL(pod.Archived, 0) = 0
      AND (poh.PurchasePrinted = 1 OR poh.PurchaseEmailed = 1)
      AND rls.SumOfQtyReceived > 0
    """


def build_ncr_supplier_coverage_sql(cfg):
    """
    How much of the NCR population the exact-key supplier path actually reaches.

    This is the measurement behind D-01 and D-03: today 51 of 378 supplier-linked NCRs
    cannot be matched to a vendor by parsed name + city. The PurchaseOrderID path
    either does better or it does not, and this says which.
    """

    src = cfg["sources"]

    return f"""
    SELECT CASE
               WHEN nc.PurchaseOrderID IS NOT NULL AND poh.PurchaseSupplierID IS NOT NULL
                   THEN 'exact key via PO'
               WHEN nc.PurchaseOrderID IS NOT NULL
                   THEN 'PO set but supplier unresolved'
               WHEN nc.Supplier IS NOT NULL
                   THEN 'name only, no PO'
               ELSE 'no supplier at all'
           END AS supplier_path,
           COUNT(*) AS ncrs
    FROM {src['ncr']} AS nc
    LEFT JOIN {src['po_header']} AS poh
           ON poh.PurchaseOrderID = nc.PurchaseOrderID
    WHERE nc.SActive = 1
    GROUP BY CASE
               WHEN nc.PurchaseOrderID IS NOT NULL AND poh.PurchaseSupplierID IS NOT NULL
                   THEN 'exact key via PO'
               WHEN nc.PurchaseOrderID IS NOT NULL
                   THEN 'PO set but supplier unresolved'
               WHEN nc.Supplier IS NOT NULL
                   THEN 'name only, no PO'
               ELSE 'no supplier at all'
           END
    ORDER BY ncrs DESC
    """
