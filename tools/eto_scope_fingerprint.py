"""
eto_scope_fingerprint.py — probe 3: identify the Excel extract's scope by its signature.

    python tools/eto_scope_fingerprint.py            # Windows auth
    python tools/eto_scope_fingerprint.py --sql-auth # ETO_USER / ETO_PWD

READ-ONLY. Paste the whole output back.

Probe 2 narrowed the PO scope to two candidates by row count alone:

    BuyerID 43              23,397 raw   ->  cleaning would drop 53   (0.23%)
    projects 9000 + 192085  23,784 raw   ->  cleaning would drop 440  (1.85%)

A row count is weak evidence — two different populations can share one. But the
extract left a whole SIGNATURE behind, recorded in docs/DESIGN.md, and a wrong scope
will not reproduce all of it at once:

    PO lines (after cleaning)      23,344
    delivery eligible              20,181
    on-time                        13,164
    late                            7,017
    commercial base-eligible       22,488
    vendor + location rows            416

Section A computes that signature for every candidate using ETO's own delivery rule
(need-by = revised else required; receipt = vwReceiverLogSummed.MaxOfDate; fully
received = SumOfQtyReceived >= PurchaseQty). Whichever candidate matches on all six
is the scope. If none does, the extract is not reproducible and the ETO run needs a
scope chosen deliberately instead.

Section B does the same for the NCR extract, section C for the vendor extract, and
section D confirms the display-name function is safe to join.
"""

import os
import sys


SERVER = os.environ.get("ETO_SERVER", r"MACRO-ETO-SVR\SQLEXPRESS")
DATABASE = os.environ.get("ETO_DATABASE", "Macrodyne_Production")
DRIVER = os.environ.get("ETO_DRIVER", "ODBC Driver 17 for SQL Server")

# The extract's recorded signature, from docs/DESIGN.md.
TARGET = {
    "lines": 23344,
    "delivery_eligible": 20181,
    "on_time": 13164,
    "late": 7017,
    "commercial_base": 22488,
    "vendor_locations": 416,
}

# Candidate scopes: label -> SQL predicate over pod / poh.
CANDIDATES = [
    ("everything",                 "1 = 1"),
    ("BuyerID 43",                 "poh.BuyerID = 43"),
    ("BuyerID 171",                "poh.BuyerID = 171"),
    ("BuyerID 166",                "poh.BuyerID = 166"),
    ("BuyerID 43 + issued",        "poh.BuyerID = 43 AND (poh.PurchasePrinted = 1 OR poh.PurchaseEmailed = 1)"),
    ("projects 9000 + 192085",     "pod.ProjectID IN (9000, 192085)"),
    ("project 9000 only",          "pod.ProjectID = 9000"),
    ("project 192085 only",        "pod.ProjectID = 192085"),
]


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
            return None
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print("  " + " | ".join(cols))
        for r in rows[:cap]:
            print("  " + " | ".join("" if v is None else str(v)[:52] for v in r))
        if len(rows) > cap:
            print(f"  ... (+{len(rows) - cap} more)")
        if not rows:
            print("  (0 rows)")
        return cols, rows
    except Exception as exc:
        print(f"  [ERROR] {type(exc).__name__}: {exc}")
        return None


# ETO's own delivery rule, transcribed from dbo.urpPurchasingLateVendors.
NEED_BY = "COALESCE(pod.DateRevised, pod.DateRequired)"
FULLY_RECEIVED = "ISNULL(rls.SumOfQtyReceived, 0) >= pod.PurchaseQty AND pod.PurchaseQty > 0"
ELIGIBLE = f"({FULLY_RECEIVED}) AND {NEED_BY} IS NOT NULL AND rls.MaxOfDate IS NOT NULL"


def fingerprint_sql(predicate):
    return f"""
    SELECT COUNT(*) AS lines,
           SUM(CASE WHEN {ELIGIBLE} THEN 1 ELSE 0 END) AS delivery_eligible,
           SUM(CASE WHEN ({ELIGIBLE}) AND rls.MaxOfDate <= {NEED_BY}
                    THEN 1 ELSE 0 END) AS on_time,
           SUM(CASE WHEN ({ELIGIBLE}) AND rls.MaxOfDate >  {NEED_BY}
                    THEN 1 ELSE 0 END) AS late,
           SUM(CASE WHEN pod.PurchaseUOM IS NOT NULL AND pod.PurchasePrice > 0
                     AND pod.ItemCompanyID IS NOT NULL AND poh.PurchaseCurr IS NOT NULL
                    THEN 1 ELSE 0 END) AS commercial_base,
           COUNT(DISTINCT poh.PurchaseSupplierID) AS distinct_suppliers
    FROM dbo.vwPurchaseOrderDetails pod
    JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
    LEFT JOIN dbo.vwReceiverLogSummed rls ON rls.PurchaseDetailID = pod.PurchaseDetailID
    WHERE {predicate}
    """


def main():
    conn = connect("--sql-auth" in sys.argv)
    cur = conn.cursor()

    try:
        rule("A. PO SCOPE FINGERPRINT — which candidate reproduces the whole signature?")

        print("\n  Target (from docs/DESIGN.md, the numbers the prototype published):")
        for key, value in TARGET.items():
            print(f"      {key:<22} {value:>8,}")
        print("\n  'lines' is POST-cleaning, so a matching candidate should be slightly")
        print("  ABOVE 23,344. Everything else should land close to the target.")
        print("  A candidate that matches on lines but misses on-time by thousands is")
        print("  a coincidence, not the scope.\n")

        results = {}

        for label, predicate in CANDIDATES:
            out = run(cur, f"A/{label}", fingerprint_sql(predicate))
            if out:
                cols, rows = out
                if rows:
                    results[label] = dict(zip(cols, rows[0]))

        # Scorecard summary across candidates, with deltas.
        if results:
            print("\n" + "=" * 78)
            print("A-SUMMARY — distance from the extract's signature (0 = exact)")
            print("=" * 78)
            header = (f"  {'candidate':<26}{'lines':>9}{'delivElig':>11}"
                      f"{'onTime':>9}{'late':>8}{'commBase':>10}")
            print(header)
            print("  " + "-" * 72)
            for label, row in results.items():
                print(f"  {label:<26}"
                      f"{(row.get('lines') or 0):>9,}"
                      f"{(row.get('delivery_eligible') or 0):>11,}"
                      f"{(row.get('on_time') or 0):>9,}"
                      f"{(row.get('late') or 0):>8,}"
                      f"{(row.get('commercial_base') or 0):>10,}")
            print("  " + "-" * 72)
            print(f"  {'TARGET':<26}{TARGET['lines']:>9,}"
                  f"{TARGET['delivery_eligible']:>11,}{TARGET['on_time']:>9,}"
                  f"{TARGET['late']:>8,}{TARGET['commercial_base']:>10,}")

        run(
            cur,
            "A9. vendor+location rows per candidate — the extract had 416",
            f"""
            SELECT 'BuyerID 43' AS candidate,
                   COUNT(DISTINCT CAST(poh.PurchaseSupplierID AS varchar(12))) AS vendor_rows
            FROM dbo.vwPurchaseOrderDetails pod
            JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
            WHERE poh.BuyerID = 43
            UNION ALL
            SELECT 'projects 9000+192085',
                   COUNT(DISTINCT CAST(poh.PurchaseSupplierID AS varchar(12)))
            FROM dbo.vwPurchaseOrderDetails pod
            JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
            WHERE pod.ProjectID IN (9000, 192085)
            """,
        )

        # ══════════════════════════════════════════════════════════════════
        rule("B. NCR SCOPE — the extract had 1,248 rows, 378 supplier-linked")

        print("\n  If the NCR extract shares the PO extract's scope, its supplier-linked")
        print("  count should follow the same buyer or project selection.\n")

        run(
            cur,
            "B1. NCRs under the PO candidates (via the NCR's own PurchaseOrderID)",
            """
            SELECT 'all active' AS candidate, COUNT(*) AS ncrs,
                   SUM(CASE WHEN nc.SupplierID IS NOT NULL THEN 1 ELSE 0 END) AS supplier_linked
            FROM dbo.vwNonConformances nc WHERE nc.SActive = 1
            UNION ALL
            SELECT 'projects 9000+192085', COUNT(*),
                   SUM(CASE WHEN nc.SupplierID IS NOT NULL THEN 1 ELSE 0 END)
            FROM dbo.vwNonConformances nc
            WHERE nc.SActive = 1 AND nc.ProjectID IN (9000, 192085)
            UNION ALL
            SELECT 'PO raised by buyer 43', COUNT(*),
                   SUM(CASE WHEN nc.SupplierID IS NOT NULL THEN 1 ELSE 0 END)
            FROM dbo.vwNonConformances nc
            JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = nc.PurchaseOrderID
            WHERE nc.SActive = 1 AND poh.BuyerID = 43
            """,
        )
        run(
            cur,
            "B2. NCRs created on or before each year end — is 1,248 just an older snapshot?",
            """
            SELECT cutoff_year,
                   (SELECT COUNT(*) FROM dbo.vwNonConformances n
                    WHERE n.SActive = 1 AND YEAR(n.CreationDate) <= y.cutoff_year) AS ncrs_to_date
            FROM (SELECT DISTINCT YEAR(CreationDate) AS cutoff_year
                  FROM dbo.vwNonConformances WHERE SActive = 1) y
            ORDER BY cutoff_year
            """,
            cap=20,
        )

        # ══════════════════════════════════════════════════════════════════
        rule("C. VENDOR SCOPE — 1,803 rows, of which only 58 were incomplete")

        print("\n  All 1,701 suppliers today include 211 incomplete (12.4%). The extract")
        print("  had 58 of 1,803 (3.2%) — a four-fold difference, so the extract is not")
        print("  the supplier table. Looking for a population that is both bigger AND")
        print("  cleaner, which usually means a different definition of 'complete'.\n")

        run(
            cur,
            "C1. incompleteness by field — which field drives the 211?",
            """
            SELECT SUM(CASE WHEN CName IS NULL OR LTRIM(RTRIM(CName)) = '' THEN 1 ELSE 0 END) AS no_name,
                   SUM(CASE WHEN CAddress1 IS NULL OR LTRIM(RTRIM(CAddress1)) = '' THEN 1 ELSE 0 END) AS no_address,
                   SUM(CASE WHEN CZip IS NULL OR LTRIM(RTRIM(CZip)) = '' THEN 1 ELSE 0 END) AS no_postcode,
                   COUNT(*) AS suppliers
            FROM dbo.tblCompany co
            WHERE EXISTS (SELECT 1 FROM dbo.tblSupplier s WHERE s.CompanyID = co.CompanyID)
            """,
        )
        run(
            cur,
            "C2. more candidate populations near 1,803",
            """
            SELECT 'supplier or has address' AS scope, COUNT(*) AS companies
              FROM dbo.tblCompany co
              WHERE EXISTS (SELECT 1 FROM dbo.tblSupplier s WHERE s.CompanyID = co.CompanyID)
                 OR (co.CAddress1 IS NOT NULL AND LTRIM(RTRIM(co.CAddress1)) <> '')
            UNION ALL SELECT 'CActive and has address', COUNT(*)
              FROM dbo.tblCompany WHERE CActive = 1
                AND CAddress1 IS NOT NULL AND LTRIM(RTRIM(CAddress1)) <> ''
            UNION ALL SELECT 'supplier or CActive', COUNT(*)
              FROM dbo.tblCompany co
              WHERE co.CActive = 1
                 OR EXISTS (SELECT 1 FROM dbo.tblSupplier s WHERE s.CompanyID = co.CompanyID)
            UNION ALL SELECT 'has a display name', COUNT(*)
              FROM dbo.udfCompanyRetrieveDisplayNames(1)
            UNION ALL SELECT 'display name and IsSupplier', COUNT(*)
              FROM dbo.udfCompanyRetrieveDisplayNames(1) WHERE IsSupplier = 1
            """,
        )

        # ══════════════════════════════════════════════════════════════════
        rule("D. DISPLAY-NAME FUNCTION — is it safe to join?")

        print("\n  The queries now LEFT JOIN dbo.udfCompanyRetrieveDisplayNames(1) on")
        print("  CompanyID. If it returns more than one row per company, every PO line")
        print("  for that supplier would be duplicated — the one failure mode that")
        print("  silently inflates every count on the scorecard.\n")

        run(
            cur,
            "D1. one row per CompanyID?  (duplicate_companies must be 0)",
            """
            SELECT COUNT(*) AS rows_returned,
                   COUNT(DISTINCT CompanyID) AS distinct_companies,
                   COUNT(*) - COUNT(DISTINCT CompanyID) AS duplicate_companies
            FROM dbo.udfCompanyRetrieveDisplayNames(1)
            """,
        )
        run(
            cur,
            "D2. does it cover every supplier we would look up?",
            """
            SELECT COUNT(*) AS suppliers,
                   SUM(CASE WHEN d.CompanyID IS NULL THEN 1 ELSE 0 END) AS missing_from_function
            FROM dbo.tblCompany co
            LEFT JOIN dbo.udfCompanyRetrieveDisplayNames(1) d ON d.CompanyID = co.CompanyID
            WHERE EXISTS (SELECT 1 FROM dbo.tblSupplier s WHERE s.CompanyID = co.CompanyID)
            """,
        )
        run(
            cur,
            "D3. Preferred vs ETO's own strings — should be 0 mismatches now",
            """
            SELECT COUNT(*) AS compared,
                   SUM(CASE WHEN nc.Supplier = d.Preferred THEN 1 ELSE 0 END) AS exact_match,
                   SUM(CASE WHEN nc.Supplier <> d.Preferred THEN 1 ELSE 0 END) AS mismatch
            FROM dbo.vwNonConformances nc
            JOIN dbo.udfCompanyRetrieveDisplayNames(1) d ON d.CompanyID = nc.SupplierID
            WHERE nc.Supplier IS NOT NULL
            """,
        )
        run(
            cur,
            "D4. what status suffixes exist? (the rebuild only knew about 'Approved')",
            """
            SELECT status_suffix, COUNT(*) AS companies FROM (
              SELECT CASE WHEN CHARINDEX(' (', Preferred) > 0
                          THEN SUBSTRING(Preferred, CHARINDEX(' (', Preferred), 40)
                          ELSE '(none)' END AS status_suffix
              FROM dbo.udfCompanyRetrieveDisplayNames(1)) x
            GROUP BY status_suffix ORDER BY companies DESC
            """,
            cap=20,
        )

        rule("PROBE 3 COMPLETE — paste everything above")

    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
