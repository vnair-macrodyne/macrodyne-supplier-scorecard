"""
eto_scope_probe.py — probe 2: the questions the schema probe left open.

    python tools/eto_scope_probe.py            # Windows auth
    python tools/eto_scope_probe.py --sql-auth # ETO_USER / ETO_PWD

READ-ONLY. Every statement is a SELECT. Paste the whole output back.

Probe 1 answered every column question. It left three things unresolved, and one of
them can silently break the whole scorecard:

  A. SUPPLIER DISPLAY NAME  ** BLOCKING **
     vwPurchaseOrderHeader.CName is the CLEAN name ("Bluewater Heater"). The Excel
     extract carried the DECORATED one ("Bluewater Heater [Oldcastle] (Approved)"),
     and the scorecard parses the city out of those brackets to form half its grain.
     Only 1 of 1,701 supplier records has a bracket stored in CName, so ETO applies
     the decoration in its display layer.

     eto_queries.DERIVED rebuilds that string. This section checks the rebuild
     character-for-character against ETO's own, and looks for the display function
     that would let us stop rebuilding it altogether.

  B. THE PO SCOPE
     No filter explains the extract. 161,392 lines; 159,464 issued; 157,223 issued
     with a receipt; the extract had 23,344 AFTER cleaning. About one seventh, and
     nothing in probe 1 produces it. This section tries project scope, receipt
     state, buyer, currency and date windows.

  C. THE NCR AND VENDOR SCOPES
     1,941 NCRs vs 1,248 extracted; 2,087 companies (1,701 suppliers) vs 1,803.
     Only the item master ties (87,237 now vs 86,730 then — the same query, earlier).

Nothing reconciles until B and C are answered, and A must be right before any ETO run
is trusted at all.
"""

import os
import sys


SERVER = os.environ.get("ETO_SERVER", r"MACRO-ETO-SVR\SQLEXPRESS")
DATABASE = os.environ.get("ETO_DATABASE", "Macrodyne_Production")
DRIVER = os.environ.get("ETO_DRIVER", "ODBC Driver 17 for SQL Server")

# The rebuild under test — must stay identical to eto_queries.DERIVED.
DISPLAY_NAME = (
    "(co.CName"
    " + CASE WHEN co.CCity IS NULL OR LTRIM(RTRIM(co.CCity)) = ''"
    " THEN '' ELSE ' [' + co.CCity + ']' END"
    " + CASE WHEN ISNULL(sup.SupQAApproved, 0) = 1"
    " THEN ' (Approved)' ELSE '' END)"
)


def connect(sql_auth=False):
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
            print("  " + " | ".join("" if v is None else str(v)[:52] for v in r))
        if len(rows) > cap:
            print(f"  ... (+{len(rows) - cap} more)")
        if not rows:
            print("  (0 rows)")
    except Exception as exc:
        print(f"  [ERROR] {type(exc).__name__}: {exc}")


def main():
    conn = connect("--sql-auth" in sys.argv)
    cur = conn.cursor()

    try:
        # ══════════════════════════════════════════════════════════════════
        rule("A. SUPPLIER DISPLAY NAME  ** BLOCKING — get this wrong and the "
             "scorecard silently loses its grain **")

        print("\n  If A1 shows 0 mismatches, the rebuild is exact and safe to ship.")
        print("  If A2 works, we can drop the rebuild and read ETO's own names.\n")

        run(
            cur,
            "A1. rebuild vs ETO's own decorated name (vwNonConformances.Supplier)",
            f"""
            SELECT COUNT(*) AS compared,
                   SUM(CASE WHEN nc.Supplier = {DISPLAY_NAME} THEN 1 ELSE 0 END) AS exact_match,
                   SUM(CASE WHEN nc.Supplier <> {DISPLAY_NAME} THEN 1 ELSE 0 END) AS mismatch
            FROM dbo.vwNonConformances nc
            JOIN dbo.tblCompany co  ON co.CompanyID = nc.SupplierID
            LEFT JOIN dbo.tblSupplier sup ON sup.CompanyID = nc.SupplierID
            WHERE nc.SupplierID IS NOT NULL AND nc.Supplier IS NOT NULL
            """,
        )
        run(
            cur,
            "A1b. any mismatches, side by side (empty = the rebuild is exact)",
            f"""
            SELECT DISTINCT TOP 25
                   nc.SupplierID, nc.Supplier AS eto_says,
                   {DISPLAY_NAME} AS we_rebuild,
                   co.CCity, sup.SupQAApproved
            FROM dbo.vwNonConformances nc
            JOIN dbo.tblCompany co  ON co.CompanyID = nc.SupplierID
            LEFT JOIN dbo.tblSupplier sup ON sup.CompanyID = nc.SupplierID
            WHERE nc.Supplier IS NOT NULL AND nc.Supplier <> {DISPLAY_NAME}
            """,
        )
        run(
            cur,
            "A1c. same test against vwReceiverLog.Supplier (a second ETO-built string)",
            f"""
            SELECT COUNT(*) AS compared,
                   SUM(CASE WHEN r.Supplier = {DISPLAY_NAME} THEN 1 ELSE 0 END) AS exact_match,
                   SUM(CASE WHEN r.Supplier <> {DISPLAY_NAME} THEN 1 ELSE 0 END) AS mismatch
            FROM (SELECT DISTINCT PurchaseSupplierID, Supplier FROM dbo.vwReceiverLog) r
            JOIN dbo.tblCompany co  ON co.CompanyID = r.PurchaseSupplierID
            LEFT JOIN dbo.tblSupplier sup ON sup.CompanyID = r.PurchaseSupplierID
            WHERE r.Supplier IS NOT NULL
            """,
        )
        run(
            cur,
            "A2. can we call ETO's own display-name function instead of rebuilding?",
            "SELECT TOP 5 * FROM dbo.udfCompanyRetrieveDisplayNames(1)",
        )
        run(
            cur,
            "A3. do any supplier cities contain a bracket that would confuse the parser?",
            "SELECT TOP 20 CompanyID, CName, CCity FROM dbo.tblCompany "
            "WHERE CCity LIKE '%[[]%' OR CCity LIKE '%]%' OR CName LIKE '%[[]%'",
        )

        # ══════════════════════════════════════════════════════════════════
        rule("B. THE PO SCOPE — what produced 23,344 lines out of 161,392?")

        print("\n  23,344 is a POST-cleaning count, so the true scope is a little above it.")
        print("  Looking for a filter that lands in the low-to-mid 20 thousands.\n")

        run(
            cur,
            "B1. lines per project, biggest 30 — is the extract a project selection?",
            "SELECT TOP 30 pod.ProjectID, COUNT(*) AS lines, MIN(poh.PurchaseDate) AS first_po, "
            "MAX(poh.PurchaseDate) AS last_po "
            "FROM dbo.vwPurchaseOrderDetails pod "
            "JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID "
            "GROUP BY pod.ProjectID ORDER BY lines DESC",
            cap=30,
        )
        run(
            cur,
            "B2. how many projects would it take to reach ~23,344 lines? (running total)",
            """
            SELECT TOP 40 ProjectID, lines,
                   SUM(lines) OVER (ORDER BY lines DESC
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                       AS running_total
            FROM (SELECT ProjectID, COUNT(*) AS lines
                  FROM dbo.vwPurchaseOrderDetails GROUP BY ProjectID) x
            ORDER BY lines DESC
            """,
            cap=40,
        )
        run(
            cur,
            "B3. receipt state — is the extract open lines, or fully-received lines?",
            """
            SELECT CASE
                     WHEN ISNULL(rls.SumOfQtyReceived, 0) = 0 THEN '1. nothing received'
                     WHEN rls.SumOfQtyReceived < pod.PurchaseQty THEN '2. partly received'
                     WHEN rls.SumOfQtyReceived = pod.PurchaseQty THEN '3. exactly received'
                     ELSE '4. over received'
                   END AS receipt_state,
                   COUNT(*) AS lines
            FROM dbo.vwPurchaseOrderDetails pod
            LEFT JOIN dbo.vwReceiverLogSummed rls ON rls.PurchaseDetailID = pod.PurchaseDetailID
            GROUP BY CASE
                     WHEN ISNULL(rls.SumOfQtyReceived, 0) = 0 THEN '1. nothing received'
                     WHEN rls.SumOfQtyReceived < pod.PurchaseQty THEN '2. partly received'
                     WHEN rls.SumOfQtyReceived = pod.PurchaseQty THEN '3. exactly received'
                     ELSE '4. over received'
                   END
            ORDER BY receipt_state
            """,
        )
        run(
            cur,
            "B4. lines with a need-by date — the extract needed one for 20,181 of its rows",
            """
            SELECT COUNT(*) AS all_lines,
                   SUM(CASE WHEN COALESCE(pod.DateRevised, pod.DateRequired) IS NOT NULL
                            THEN 1 ELSE 0 END) AS with_need_by,
                   SUM(CASE WHEN COALESCE(pod.DateRevised, pod.DateRequired) IS NOT NULL
                             AND rls.MaxOfDate IS NOT NULL
                             AND rls.SumOfQtyReceived >= pod.PurchaseQty
                            THEN 1 ELSE 0 END) AS delivery_eligible
            FROM dbo.vwPurchaseOrderDetails pod
            LEFT JOIN dbo.vwReceiverLogSummed rls ON rls.PurchaseDetailID = pod.PurchaseDetailID
            """,
        )
        run(
            cur,
            "B5. rolling windows back from today — does a date range give ~23,344?",
            """
            SELECT '12 months' AS window, COUNT(*) AS lines FROM dbo.vwPurchaseOrderDetails pod
              JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
              WHERE poh.PurchaseDate >= DATEADD(month, -12, GETDATE())
            UNION ALL SELECT '18 months', COUNT(*) FROM dbo.vwPurchaseOrderDetails pod
              JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
              WHERE poh.PurchaseDate >= DATEADD(month, -18, GETDATE())
            UNION ALL SELECT '24 months', COUNT(*) FROM dbo.vwPurchaseOrderDetails pod
              JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
              WHERE poh.PurchaseDate >= DATEADD(month, -24, GETDATE())
            """,
        )
        run(
            cur,
            "B6. by buyer — was it one buyer's report?",
            "SELECT TOP 20 poh.BuyerID, COUNT(*) AS lines FROM dbo.vwPurchaseOrderDetails pod "
            "JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID "
            "GROUP BY poh.BuyerID ORDER BY lines DESC",
            cap=20,
        )
        run(
            cur,
            "B7. distinct POs and suppliers overall — the extract had 416 vendor+location rows",
            "SELECT COUNT(DISTINCT pod.PurchaseOrderID) AS distinct_pos, "
            "COUNT(DISTINCT poh.PurchaseSupplierID) AS distinct_suppliers "
            "FROM dbo.vwPurchaseOrderDetails pod "
            "JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID",
        )

        # ══════════════════════════════════════════════════════════════════
        rule("C. THE NCR AND VENDOR SCOPES")

        run(
            cur,
            "C1. NCRs by year, newest first with a running total — 1,248 was the extract",
            """
            SELECT created_year, ncrs,
                   SUM(ncrs) OVER (ORDER BY created_year DESC
                                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                       AS running_total
            FROM (SELECT YEAR(CreationDate) AS created_year, COUNT(*) AS ncrs
                  FROM dbo.vwNonConformances WHERE SActive = 1
                  GROUP BY YEAR(CreationDate)) x
            ORDER BY created_year DESC
            """,
            cap=20,
        )
        run(
            cur,
            "C2. NCR sub-populations — does any of them give 1,248?",
            """
            SELECT 'has barcode' AS scope, COUNT(*) AS ncrs
              FROM dbo.vwNonConformances WHERE SActive = 1 AND NonConformanceBarcode IS NOT NULL
            UNION ALL SELECT 'resolved', COUNT(*)
              FROM dbo.vwNonConformances WHERE SActive = 1 AND Resolved = 1
            UNION ALL SELECT 'unresolved', COUNT(*)
              FROM dbo.vwNonConformances WHERE SActive = 1 AND Resolved = 0
            UNION ALL SELECT 'released not null', COUNT(*)
              FROM dbo.vwNonConformances WHERE SActive = 1 AND Released IS NOT NULL
            UNION ALL SELECT 'quantity populated', COUNT(*)
              FROM dbo.vwNonConformances WHERE SActive = 1 AND Quantity IS NOT NULL
            UNION ALL SELECT 'on vwNonConformanceList', COUNT(*)
              FROM dbo.vwNonConformanceList
            """,
        )
        run(
            cur,
            "C3. vendor sub-populations — does any of them give 1,803?",
            """
            SELECT 'supplier OR purchased-from' AS scope, COUNT(*) AS companies
              FROM dbo.tblCompany co
              WHERE EXISTS (SELECT 1 FROM dbo.tblSupplier s WHERE s.CompanyID = co.CompanyID)
                 OR EXISTS (SELECT 1 FROM dbo.vwPurchaseOrderHeader h
                            WHERE h.PurchaseSupplierID = co.CompanyID)
            UNION ALL SELECT 'not a project customer', COUNT(*)
              FROM dbo.tblCompany co
              WHERE NOT EXISTS (SELECT 1 FROM dbo.tblProjects p WHERE p.CompanyID = co.CompanyID)
            UNION ALL SELECT 'CActive = 1 and not a customer', COUNT(*)
              FROM dbo.tblCompany co
              WHERE co.CActive = 1
                AND NOT EXISTS (SELECT 1 FROM dbo.tblProjects p WHERE p.CompanyID = co.CompanyID)
            UNION ALL SELECT 'has a mailing address', COUNT(*)
              FROM dbo.tblCompany WHERE CAddress1 IS NOT NULL AND LTRIM(RTRIM(CAddress1)) <> ''
            UNION ALL SELECT 'supplier with address and postcode', COUNT(*)
              FROM dbo.tblCompany co
              WHERE EXISTS (SELECT 1 FROM dbo.tblSupplier s WHERE s.CompanyID = co.CompanyID)
                AND CAddress1 IS NOT NULL AND CZip IS NOT NULL
            """,
        )
        run(
            cur,
            "C4. the extract's own quality findings were 58 incomplete + 8 exact duplicates",
            """
            SELECT SUM(CASE WHEN CName IS NULL OR LTRIM(RTRIM(CName)) = ''
                             OR CAddress1 IS NULL OR LTRIM(RTRIM(CAddress1)) = ''
                             OR CZip IS NULL OR LTRIM(RTRIM(CZip)) = ''
                        THEN 1 ELSE 0 END) AS incomplete_suppliers,
                   COUNT(*) AS suppliers
            FROM dbo.tblCompany co
            WHERE EXISTS (SELECT 1 FROM dbo.tblSupplier s WHERE s.CompanyID = co.CompanyID)
            """,
        )

        # ══════════════════════════════════════════════════════════════════
        rule("D. LEAD TIME — probe 1 overturned the 'empty on every item' finding")

        print("\n  844 of 87,237 items carry a value; only 114 are positive. A zero is")
        print("  'nobody entered one', but the evaluator would treat it as a real")
        print("  zero-day promise and mark every such line late. Confirming the split,")
        print("  and whether the populated items are ones we actually buy.\n")

        run(
            cur,
            "D1. lead-time value distribution",
            """
            SELECT CASE WHEN EstimatedLeadTime IS NULL THEN 'NULL'
                        WHEN EstimatedLeadTime = 0 THEN 'zero'
                        WHEN EstimatedLeadTime < 0 THEN 'negative'
                        WHEN EstimatedLeadTime BETWEEN 1 AND 14 THEN '1-14 days'
                        WHEN EstimatedLeadTime BETWEEN 15 AND 45 THEN '15-45 days'
                        ELSE 'over 45 days' END AS lead_time_band,
                   COUNT(*) AS items
            FROM dbo.tblEngItemMaster
            GROUP BY CASE WHEN EstimatedLeadTime IS NULL THEN 'NULL'
                        WHEN EstimatedLeadTime = 0 THEN 'zero'
                        WHEN EstimatedLeadTime < 0 THEN 'negative'
                        WHEN EstimatedLeadTime BETWEEN 1 AND 14 THEN '1-14 days'
                        WHEN EstimatedLeadTime BETWEEN 15 AND 45 THEN '15-45 days'
                        ELSE 'over 45 days' END
            ORDER BY items DESC
            """,
        )
        run(
            cur,
            "D2. how many PO lines would actually become lead-time eligible?",
            """
            SELECT COUNT(*) AS po_lines_with_positive_lead_time
            FROM dbo.vwPurchaseOrderDetails pod
            JOIN dbo.tblEngItemMaster im ON im.ItemID = pod.ItemID
            WHERE im.EstimatedLeadTime > 0
            """,
        )

        rule("PROBE 2 COMPLETE — paste everything above")

    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
