"""
eto_schema_probe.py — read-only discovery for the Vendor Scorecard's ETO migration.

Run this ON a machine that can reach MACRO-ETO-SVR (the ETO server itself is fine):

    python eto_schema_probe.py            # Windows auth
    set ETO_USER=... & set ETO_PWD=...    # or SQL auth
    python eto_schema_probe.py --sql-auth

Then paste the whole output back.

NOTHING IS WRITTEN. Every statement is a SELECT against the read-only reporting account.

What it answers, in order:

  A. Object inventory        do the views the queries name actually exist
  B. Column inventory        the real column list for each object
  C. Unresolved fields       which column carries UOM, NCR quantities, part number, ...
  D. PO scope census         explains the Excel extract's 23,344 lines vs ~159,199
  E. NCR supplier coverage   how far the exact-key path reaches vs parsed names
  F. Receipt history         is event-level receiving available (vwReceiverLog)
  G. Vendor scope            which filter reproduces the 1,803-row vendor extract
  G4. NCR + item census      the other two extracts nobody has scoped either
  H. Lead-time reality       confirm EstimatedLeadTime is still empty

Most of the columns the queries need are already named in the project's own verified
discovery documents, so section C is now largely CONFIRMATION rather than discovery. The
genuinely open one is the item master: `ItemCompanyID` is assumed to be the item number,
and it is load-bearing twice over -- it is the PO required field `part_number` AND the
lead-time match key. If it is wrong, section C tells you what to use instead.

Sections D and G matter most of all, because the scope of the original Excel extract was
never recorded and nothing can be reconciled until it is.
"""

import os
import sys


# ── objects the scorecard queries depend on ───────────────────────────────────
OBJECTS = [
    "vwPurchaseOrderHeader", "vwPurchaseOrderDetails", "vwReceiverLogSummed",
    "vwReceiverLog", "tblReceiverLog",
    "vwNonConformances", "vwNonConformanceList", "vwCostingSummed_ByNC",
    "tlkpNonConformanceOrigin",
    "tblEngItemMaster", "vwEngItemMaster", "vwInventory",
    "tblCompany", "tblSupplier", "vwSupplier", "tblProjects", "vwProjects",
]

# ── the fields config/eto.json cannot yet name, and what to look for ──────────
# label -> (object, [substrings that would appear in the right column name])
UNRESOLVED = {
    # ---- genuinely open ----
    "Item: part number  ** LOAD-BEARING (PO required field + lead-time key) **":
        ("tblEngItemMaster", ["itemcompany", "partnumber", "itemnumber", "itemno"]),
    "Item: description":
        ("tblEngItemMaster", ["description", "itemdesc"]),
    "Item: UOM":
        ("tblEngItemMaster", ["uom", "measure"]),
    "Item: category":
        ("tblEngItemMaster", ["category", "class", "group", "type"]),
    "Item: list price / LPP":
        ("tblEngItemMaster", ["price", "cost", "lpp"]),
    "Item: revision":
        ("tblEngItemMaster", ["rev"]),
    "Item: preferred / last supplier":
        ("tblEngItemMaster", ["supplier", "vendor", "preferred"]),
    "Item: manufacturer":
        ("tblEngItemMaster", ["manuf", "mfg", "maker"]),
    "Item: quantity reserved":
        ("tblEngItemMaster", ["reserv", "allocat", "commit"]),
    "PO line: supplier's part number (not consumed today)":
        ("vwPurchaseOrderDetails", ["supplierpart", "vendorpart", "manufpart", "suppart"]),
    "PO header: a separate order number (not consumed today)":
        ("vwPurchaseOrderHeader", ["ordernumber", "orderno", "purchaseordernumber", "ponumber"]),
    "NCR: hours (not consumed today)":
        ("vwNonConformances", ["hour", "labour", "labor"]),

    # ---- confirmations: named in prior discovery, verify they are on the VIEW ----
    "CONFIRM PO line: PurchaseUOM  ** LOAD-BEARING **":
        ("vwPurchaseOrderDetails", ["uom", "measure"]),
    "CONFIRM PO line: ItemID / ItemDescription":
        ("vwPurchaseOrderDetails", ["itemid", "itemdescription"]),
    "CONFIRM PO line: LastReceivedDate":
        ("vwPurchaseOrderDetails", ["lastreceiv", "receiveddate", "recddate"]),
    "CONFIRM PO header: PurchasePrinted / PurchaseEmailed (verified on the TABLE only)":
        ("vwPurchaseOrderHeader", ["printed", "emailed", "purchaseactive"]),
    "CONFIRM NCR: Quantity / QuantityRejected  ** LOAD-BEARING **":
        ("vwNonConformances", ["qty", "quantity", "reject"]),
    "CONFIRM NCR: SpecID / ItemID / RecommendedInterim / QualityFollowUp":
        ("vwNonConformances", ["specid", "itemid", "interim", "followup"]),
    "CONFIRM NCR: NonConformanceCustom5 (captioned 'Target Date')":
        ("vwNonConformances", ["custom"]),
    "CONFIRM Vendor: address block":
        ("tblCompany", ["address", "city", "state", "zip", "postal", "country", "prov", "active"]),
    "CONFIRM Supplier extension attributes":
        ("tblSupplier", []),
}


# ── connection ────────────────────────────────────────────────────────────────
SERVER = os.environ.get("ETO_SERVER", r"MACRO-ETO-SVR\SQLEXPRESS")
DATABASE = os.environ.get("ETO_DATABASE", "Macrodyne_Production")
DRIVER = os.environ.get("ETO_DRIVER", "ODBC Driver 17 for SQL Server")


def connect(sql_auth=False):
    # Reuse the Project Console's proven connector when this runs beside it.
    if not sql_auth:
        try:
            from console_store import eto_connection
            print("[connection] using console_store.eto_connection()")
            return eto_connection()
        except Exception:
            pass

    import pyodbc

    cs = f"Driver={{{DRIVER}}};Server={SERVER};Database={DATABASE};"

    if sql_auth:
        user, pwd = os.environ.get("ETO_USER"), os.environ.get("ETO_PWD")
        if not user or not pwd:
            sys.exit("Set ETO_USER and ETO_PWD for --sql-auth.")
        cs += f"UID={user};PWD={pwd};"
    else:
        cs += "Trusted_Connection=yes;"

    print(f"[connection] {SERVER} / {DATABASE}")
    return pyodbc.connect(cs, timeout=15)


# ── output helpers ────────────────────────────────────────────────────────────
def rule(title):
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def run(cur, label, sql, params=(), cap=40):
    print("\n" + "-" * 78 + f"\n{label}\n" + "-" * 78)
    try:
        cur.execute(sql, params)
        while cur.description is None and cur.nextset():
            pass
        if cur.description is None:
            print("  (no result set)")
            return
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print("  " + " | ".join(cols))
        for r in rows[:cap]:
            print("  " + " | ".join("" if v is None else str(v)[:48] for v in r))
        if len(rows) > cap:
            print(f"  ... (+{len(rows) - cap} more)")
        if not rows:
            print("  (0 rows)")
    except Exception as exc:
        print(f"  [ERROR] {type(exc).__name__}: {exc}")


def columns_of(cur, obj):
    try:
        cur.execute(
            "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE "
            "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ? "
            "ORDER BY ORDINAL_POSITION",
            (obj,),
        )
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]
    except Exception as exc:
        print(f"  [ERROR reading columns of {obj}] {exc}")
        return []


def main():
    sql_auth = "--sql-auth" in sys.argv

    conn = connect(sql_auth)
    cur = conn.cursor()

    try:
        # ── A ────────────────────────────────────────────────────────────────
        rule("A. OBJECT INVENTORY — do the objects the queries name exist?")
        run(
            cur,
            "A1. presence and type",
            "SELECT TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_NAME IN ({}) ORDER BY TABLE_NAME".format(
                ",".join("?" for _ in OBJECTS)
            ),
            tuple(OBJECTS),
            cap=60,
        )
        run(
            cur,
            "A2. anything else PO / receiver / nonconformance shaped we may have missed",
            "SELECT TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_NAME LIKE '%Purchase%' OR TABLE_NAME LIKE '%Receiv%' "
            "OR TABLE_NAME LIKE '%NonConformance%' OR TABLE_NAME LIKE '%Supplier%' "
            "ORDER BY TABLE_NAME",
            cap=80,
        )

        # ── B ────────────────────────────────────────────────────────────────
        rule("B. COLUMN INVENTORY")
        inventory = {}
        for obj in OBJECTS:
            cols = columns_of(cur, obj)
            if not cols:
                print(f"\n  {obj}: (does not exist / not readable)")
                continue
            inventory[obj] = cols
            print(f"\n  {obj} ({len(cols)} columns):")
            for name, dtype, nullable in cols:
                print(f"    {name} : {dtype}{'' if nullable == 'YES' else ' NOT NULL'}")

        # ── C ────────────────────────────────────────────────────────────────
        rule("C. UNRESOLVED FIELDS — which column carries each one?")
        print("\n  Matching by name substring. Confirm the pick, then set it in")
        print("  config/eto.json under columns.<dataset>.<field>.\n")

        for label, (obj, hints) in UNRESOLVED.items():
            cols = inventory.get(obj)
            if cols is None:
                print(f"  {label}\n      -> {obj} not available\n")
                continue

            if hints:
                hits = [
                    (n, t) for n, t, _ in cols
                    if any(h in n.lower() for h in hints)
                ]
            else:
                hits = [(n, t) for n, t, _ in cols]

            print(f"  {label}   [{obj}]")
            if hits:
                for name, dtype in hits[:14]:
                    print(f"      candidate: {name} : {dtype}")
                if len(hits) > 14:
                    print(f"      ... (+{len(hits) - 14} more)")
            else:
                print("      NO CANDIDATE — the field may not exist on this object")
            print()

        # Populated-ness of the load-bearing candidates, so a column that exists but
        # is empty is not mistaken for a solution.
        rule("C2. ARE THE LOAD-BEARING CANDIDATES ACTUALLY POPULATED?")
        for obj, hints in (
            ("vwPurchaseOrderDetails", ["uom", "measure"]),
            ("vwNonConformances", ["quantity", "reject"]),
            ("tblEngItemMaster", ["itemcompany", "partnumber", "itemnumber"]),
        ):
            for name, dtype, _ in inventory.get(obj, []):
                if not any(h in name.lower() for h in hints):
                    continue
                run(
                    cur,
                    f"{obj}.{name} ({dtype}) — population",
                    # COUNT([col]) errors on text/ntext; CASE works on every type.
                    f"SELECT COUNT(*) AS rows_total, "
                    f"SUM(CASE WHEN [{name}] IS NULL THEN 0 ELSE 1 END) AS rows_populated "
                    f"FROM dbo.[{obj}]",
                )

        # ── D ────────────────────────────────────────────────────────────────
        rule("D. PO SCOPE CENSUS — what makes 23,344 out of ~159,199?")
        print("\n  The Excel prototype scored 23,344 PO lines AFTER cleaning (header-row")
        print("  removal + the required-field split). The raw counts below are BEFORE that,")
        print("  so the right scope lands somewhat ABOVE 23,344, not exactly on it.")
        print("  'Nearest 23,344' is the wrong test; 'slightly above, and explicable' is right.\n")
        run(
            cur,
            "D1. line counts by candidate scope",
            """
            SELECT 'all lines' AS scope, COUNT(*) AS lines FROM dbo.vwPurchaseOrderDetails
            UNION ALL SELECT 'header active', COUNT(*)
              FROM dbo.vwPurchaseOrderDetails pod
              JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
              WHERE poh.PurchaseActive = 1
            UNION ALL SELECT 'active + not archived', COUNT(*)
              FROM dbo.vwPurchaseOrderDetails pod
              JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
              WHERE poh.PurchaseActive = 1 AND ISNULL(pod.Archived, 0) = 0
            UNION ALL SELECT 'active + not archived + issued', COUNT(*)
              FROM dbo.vwPurchaseOrderDetails pod
              JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
              WHERE poh.PurchaseActive = 1 AND ISNULL(pod.Archived, 0) = 0
                AND (poh.PurchasePrinted = 1 OR poh.PurchaseEmailed = 1)
            UNION ALL SELECT 'issued + has receipt', COUNT(*)
              FROM dbo.vwPurchaseOrderDetails pod
              JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
              LEFT JOIN dbo.vwReceiverLogSummed rls ON rls.PurchaseDetailID = pod.PurchaseDetailID
              WHERE poh.PurchaseActive = 1 AND ISNULL(pod.Archived, 0) = 0
                AND (poh.PurchasePrinted = 1 OR poh.PurchaseEmailed = 1)
                AND rls.SumOfQtyReceived > 0
            """,
        )
        run(
            cur,
            "D2. issued lines by PO year — is the extract a date window?",
            "SELECT YEAR(poh.PurchaseDate) AS po_year, COUNT(*) AS lines "
            "FROM dbo.vwPurchaseOrderDetails pod "
            "JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID "
            "WHERE poh.PurchaseActive = 1 AND ISNULL(pod.Archived, 0) = 0 "
            "  AND (poh.PurchasePrinted = 1 OR poh.PurchaseEmailed = 1) "
            "GROUP BY YEAR(poh.PurchaseDate) ORDER BY po_year DESC",
            cap=30,
        )

        # ── E ────────────────────────────────────────────────────────────────
        rule("E. NCR SUPPLIER COVERAGE — exact key vs parsed name")
        print("\n  Today 51 of 378 supplier-linked NCRs cannot be matched to a vendor by")
        print("  parsed name + city. This is whether PurchaseOrderID does better.\n")
        run(
            cur,
            "E1. supplier attribution path",
            """
            SELECT CASE
                     WHEN nc.PurchaseOrderID IS NOT NULL AND poh.PurchaseSupplierID IS NOT NULL
                          THEN '1. exact key via PO'
                     WHEN nc.PurchaseOrderID IS NOT NULL THEN '2. PO set, supplier unresolved'
                     WHEN nc.Supplier IS NOT NULL        THEN '3. name only, no PO'
                     ELSE '4. no supplier at all'
                   END AS supplier_path,
                   COUNT(*) AS ncrs
            FROM dbo.vwNonConformances nc
            LEFT JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = nc.PurchaseOrderID
            WHERE nc.SActive = 1
            GROUP BY CASE
                     WHEN nc.PurchaseOrderID IS NOT NULL AND poh.PurchaseSupplierID IS NOT NULL
                          THEN '1. exact key via PO'
                     WHEN nc.PurchaseOrderID IS NOT NULL THEN '2. PO set, supplier unresolved'
                     WHEN nc.Supplier IS NOT NULL        THEN '3. name only, no PO'
                     ELSE '4. no supplier at all'
                   END
            ORDER BY supplier_path
            """,
        )
        run(
            cur,
            "E2. does nc.Supplier agree with the PO's supplier where both exist?",
            """
            SELECT TOP 20 nc.NonConformanceID, nc.Supplier AS ncr_supplier,
                   poh.CName AS po_supplier, poh.PurchaseSupplierID
            FROM dbo.vwNonConformances nc
            JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = nc.PurchaseOrderID
            WHERE nc.SActive = 1 AND nc.Supplier IS NOT NULL
              AND nc.Supplier <> poh.CName
            """,
        )

        # ── F ────────────────────────────────────────────────────────────────
        rule("F. RECEIPT HISTORY — is event-level receiving available?")
        print("\n  vwReceiverLogSummed gives one row per line (last date, total qty).")
        print("  An event-level log would let On-Time Delivery judge partial receipts")
        print("  instead of excluding them, which is the single biggest OTD improvement.\n")
        for obj in ("vwReceiverLog", "tblReceiverLog"):
            if obj in inventory:
                run(cur, f"F/{obj} — sample rows", f"SELECT TOP 10 * FROM dbo.[{obj}]")
                run(
                    cur,
                    f"F/{obj} — receipts per PO line (is it really event level?)",
                    f"SELECT receipts_per_line, COUNT(*) AS lines FROM ("
                    f"  SELECT PurchaseDetailID, COUNT(*) AS receipts_per_line "
                    f"  FROM dbo.[{obj}] GROUP BY PurchaseDetailID) x "
                    f"GROUP BY receipts_per_line ORDER BY receipts_per_line",
                    cap=20,
                )

        # ── G ────────────────────────────────────────────────────────────────
        rule("G. VENDOR SCOPE — which filter reproduces the 1,803-row extract?")
        run(
            cur,
            "G1. company counts by scope",
            """
            SELECT 'all companies' AS scope, COUNT(*) AS companies FROM dbo.tblCompany
            UNION ALL SELECT 'in tblSupplier', COUNT(*)
              FROM dbo.tblCompany co
              WHERE EXISTS (SELECT 1 FROM dbo.tblSupplier s WHERE s.CompanyID = co.CompanyID)
            UNION ALL SELECT 'purchased from (any PO)', COUNT(*)
              FROM dbo.tblCompany co
              WHERE EXISTS (SELECT 1 FROM dbo.vwPurchaseOrderHeader h
                            WHERE h.PurchaseSupplierID = co.CompanyID)
            UNION ALL SELECT 'in tblSupplier AND CActive = 1', COUNT(*)
              FROM dbo.tblCompany co
              WHERE co.CActive = 1
                AND EXISTS (SELECT 1 FROM dbo.tblSupplier s WHERE s.CompanyID = co.CompanyID)
            UNION ALL SELECT 'CActive = 1 (any company)', COUNT(*)
              FROM dbo.tblCompany WHERE CActive = 1
            UNION ALL SELECT 'a project customer', COUNT(*)
              FROM dbo.tblCompany co
              WHERE EXISTS (SELECT 1 FROM dbo.tblProjects p WHERE p.CompanyID = co.CompanyID)
            """,
        )
        run(
            cur,
            "G2. does CName really carry the 'NAME [CITY] (Approved)' convention?",
            "SELECT TOP 15 CompanyID, CName, CCity FROM dbo.tblCompany "
            "WHERE CName LIKE '%[[]%]%' ORDER BY CompanyID",
        )
        run(
            cur,
            "G3. how many supplier names carry a bracketed location at all?",
            "SELECT SUM(CASE WHEN CName LIKE '%[[]%]%' THEN 1 ELSE 0 END) AS with_bracket, "
            "SUM(CASE WHEN CName LIKE '%[[]%]%' THEN 0 ELSE 1 END) AS without_bracket, "
            "COUNT(*) AS total FROM dbo.tblCompany co "
            "WHERE EXISTS (SELECT 1 FROM dbo.tblSupplier s WHERE s.CompanyID = co.CompanyID)",
        )

        # ── G4 ───────────────────────────────────────────────────────────────
        rule("G4. NCR AND ITEM CENSUS — the other two unexplained extracts")
        print("\n  The scope question is not only about POs. The Excel extracts held")
        print("  1,248 NCRs and 86,730 items. Both will show large unexplained diffs in")
        print("  reconcile_sources.py until these populations are understood too.\n")
        run(
            cur,
            "G4a. NCR population vs the 1,248-row extract",
            """
            SELECT 'all NCRs' AS scope, COUNT(*) AS ncrs FROM dbo.vwNonConformances
            UNION ALL SELECT 'SActive = 1', COUNT(*)
              FROM dbo.vwNonConformances WHERE SActive = 1
            UNION ALL SELECT 'SActive = 1 AND supplier set', COUNT(*)
              FROM dbo.vwNonConformances WHERE SActive = 1 AND Supplier IS NOT NULL
            UNION ALL SELECT 'SActive = 1 AND PO set', COUNT(*)
              FROM dbo.vwNonConformances WHERE SActive = 1 AND PurchaseOrderID IS NOT NULL
            """,
        )
        run(
            cur,
            "G4b. NCRs by creation year — is the extract a date window?",
            "SELECT YEAR(CreationDate) AS created_year, COUNT(*) AS ncrs "
            "FROM dbo.vwNonConformances WHERE SActive = 1 "
            "GROUP BY YEAR(CreationDate) ORDER BY created_year DESC",
            cap=30,
        )
        run(
            cur,
            "G4c. Item master population vs the 86,730-row extract",
            "SELECT COUNT(*) AS items FROM dbo.tblEngItemMaster",
        )

        # ── H ────────────────────────────────────────────────────────────────
        rule("H. LEAD-TIME REALITY CHECK")
        print("\n  Verified 2026-07-25: EstimatedLeadTime is empty on every item, which is")
        print("  why the Lead-Time component is permanently N/A. Confirming it still holds —")
        print("  if this is still 0, migrating to ETO does not revive the component.\n")
        run(
            cur,
            "H1. EstimatedLeadTime population",
            "SELECT COUNT(*) AS items, COUNT(EstimatedLeadTime) AS with_lead_time, "
            "SUM(CASE WHEN EstimatedLeadTime > 0 THEN 1 ELSE 0 END) AS positive_lead_time "
            "FROM dbo.tblEngItemMaster",
        )

        rule("PROBE COMPLETE — paste everything above")

    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
