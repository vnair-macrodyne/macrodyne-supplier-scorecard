"""
eto_datebasis_probe.py — probe 4: why is the extract 65% on-time when ETO is 46-49%?

    python tools/eto_datebasis_probe.py --sql-auth

READ-ONLY. Paste the whole output back.

Probe 3 ruled out both PO scope candidates, and the way it ruled them out is the
finding. Every ETO population lands at 46-49% on-time; the Excel extract is 65.2%:

                          lines    delivery elig.   on-time %   vendors
    the extract          23,344         20,181        65.2%        416
    BuyerID 43           23,397         23,202        44.8%        242
    projects 9000+192085 23,784         20,357        45.6%        380
    everything          161,392        152,026        49.0%      1,054

A subset cannot beat the whole population by 16 points on this measure. Whatever
produced the extract was not computing on-time the way this probe does, so the
question is no longer "which scope" but "which DATES".

Section A holds the population fixed at everything and varies the date basis until
one of them produces ~65%. Section B re-runs the winning basis against the scope
candidates. Section C confirms the display-name column after probe 3 caught the
wrong one. Section D chases the vendor extract, which now has a strong candidate.

The prototype's rule, for reference (delivery_evaluator.py):
    target   = revised else required
    eligible = fully received AND target present AND receipt date present
    on time   = receipt <= target
"""

import os
import sys


SERVER = os.environ.get("ETO_SERVER", r"MACRO-ETO-SVR\SQLEXPRESS")
DATABASE = os.environ.get("ETO_DATABASE", "Macrodyne_Production")
DRIVER = os.environ.get("ETO_DRIVER", "ODBC Driver 17 for SQL Server")

TARGET_ELIGIBLE = 20181
TARGET_ON_TIME = 13164
TARGET_ON_TIME_PCT = 65.2


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
            print("  " + " | ".join("" if v is None else str(v)[:56] for v in r))
        if len(rows) > cap:
            print(f"  ... (+{len(rows) - cap} more)")
        if not rows:
            print("  (0 rows)")
    except Exception as exc:
        print(f"  [ERROR] {type(exc).__name__}: {exc}")


# Candidate need-by dates and candidate receipt dates.
NEED_BY = {
    "detail revised-else-required": "COALESCE(pod.DateRevised, pod.DateRequired)",
    "detail required only":          "pod.DateRequired",
    "detail revised only":           "pod.DateRevised",
    "header revised-else-required":  "COALESCE(poh.PurchaseDateRevised, poh.PurchaseDateRequired)",
    "header required only":          "poh.PurchaseDateRequired",
    "detail else header":            ("COALESCE(pod.DateRevised, pod.DateRequired,"
                                      " poh.PurchaseDateRevised, poh.PurchaseDateRequired)"),
}

RECEIPT = {
    "last receipt (MaxOfDate)": "rls.MaxOfDate",
    "first receipt (MinOfDate)": "firstr.MinOfDate",
}

FULLY_RECEIVED = "ISNULL(rls.SumOfQtyReceived, 0) >= pod.PurchaseQty AND pod.PurchaseQty > 0"

FROM_CLAUSE = """
    FROM dbo.vwPurchaseOrderDetails pod
    JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
    LEFT JOIN dbo.vwReceiverLogSummed rls ON rls.PurchaseDetailID = pod.PurchaseDetailID
    LEFT JOIN (SELECT PurchaseDetailID, MIN([Date]) AS MinOfDate
               FROM dbo.vwReceiverLog GROUP BY PurchaseDetailID) firstr
           ON firstr.PurchaseDetailID = pod.PurchaseDetailID
"""


def basis_sql(need_by, receipt, predicate="1 = 1", day_granularity=False):
    eligible = f"({FULLY_RECEIVED}) AND {need_by} IS NOT NULL AND {receipt} IS NOT NULL"
    if day_granularity:
        on_time = f"DATEDIFF(day, {need_by}, {receipt}) <= 0"
    else:
        on_time = f"{receipt} <= {need_by}"
    return f"""
    SELECT COUNT(*) AS lines,
           SUM(CASE WHEN {eligible} THEN 1 ELSE 0 END) AS delivery_eligible,
           SUM(CASE WHEN ({eligible}) AND {on_time} THEN 1 ELSE 0 END) AS on_time
    {FROM_CLAUSE}
    WHERE {predicate}
    """


def main():
    conn = connect("--sql-auth" in sys.argv)
    cur = conn.cursor()

    try:
        rule("A. DATE BASIS — which combination gives ~65% on-time?")
        print(f"\n  Target: {TARGET_ON_TIME:,} on-time of {TARGET_ELIGIBLE:,} eligible "
              f"= {TARGET_ON_TIME_PCT}%")
        print("  Population held at EVERYTHING, so only the dates vary.\n")

        for need_label, need_sql in NEED_BY.items():
            for recv_label, recv_sql in RECEIPT.items():
                for day in (False, True):
                    tag = "day-granularity" if day else "timestamp"
                    run(cur,
                        f"A/ need-by = {need_label}  |  receipt = {recv_label}  |  {tag}",
                        basis_sql(need_sql, recv_sql, day_granularity=day))

        rule("B. THE WINNING BASIS AGAINST EACH SCOPE CANDIDATE")
        print("\n  Re-run whichever basis in section A landed near 65% against the")
        print("  scope candidates. Detail revised-else-required is repeated here as")
        print("  the control.\n")

        for label, predicate in (
            ("everything", "1 = 1"),
            ("BuyerID 43", "poh.BuyerID = 43"),
            ("projects 9000+192085", "pod.ProjectID IN (9000, 192085)"),
        ):
            run(cur, f"B/{label} — header revised-else-required, day granularity",
                basis_sql(NEED_BY["header revised-else-required"],
                          RECEIPT["last receipt (MaxOfDate)"],
                          predicate, day_granularity=True))

        run(cur,
            "B9. how often do the detail and header need-by dates actually differ?",
            """
            SELECT COUNT(*) AS lines,
                   SUM(CASE WHEN COALESCE(pod.DateRevised, pod.DateRequired)
                             = COALESCE(poh.PurchaseDateRevised, poh.PurchaseDateRequired)
                            THEN 1 ELSE 0 END) AS same,
                   SUM(CASE WHEN COALESCE(pod.DateRevised, pod.DateRequired)
                            <> COALESCE(poh.PurchaseDateRevised, poh.PurchaseDateRequired)
                            THEN 1 ELSE 0 END) AS different,
                   SUM(CASE WHEN COALESCE(poh.PurchaseDateRevised, poh.PurchaseDateRequired)
                            IS NULL THEN 1 ELSE 0 END) AS header_null
            FROM dbo.vwPurchaseOrderDetails pod
            JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
            """)

        # ══════════════════════════════════════════════════════════════════
        rule("C. DISPLAY NAME — confirming the right column after probe 3's type error")

        print("\n  probe 3 D3/D4 failed with 'cannot convert nvarchar to bit'. The")
        print("  display name is CompanyCity; 'Preferred' is a BIT (is-preferred-")
        print("  supplier). Reading Preferred would have made every vendor_name '1'.\n")

        run(cur, "C1. the two columns side by side",
            "SELECT TOP 8 CompanyID, CompanyCity, CompanyCityNoStatus, Preferred "
            "FROM dbo.udfCompanyRetrieveDisplayNames(1) ORDER BY CompanyID")
        run(cur, "C2. CompanyCity vs ETO's own strings — expect 0 mismatches",
            """
            SELECT COUNT(*) AS compared,
                   SUM(CASE WHEN nc.Supplier = d.CompanyCity THEN 1 ELSE 0 END) AS exact_match,
                   SUM(CASE WHEN nc.Supplier <> d.CompanyCity THEN 1 ELSE 0 END) AS mismatch
            FROM dbo.vwNonConformances nc
            JOIN dbo.udfCompanyRetrieveDisplayNames(1) d ON d.CompanyID = nc.SupplierID
            WHERE nc.Supplier IS NOT NULL
            """)
        run(cur, "C3. same against the receiver log",
            """
            SELECT COUNT(*) AS compared,
                   SUM(CASE WHEN r.Supplier = d.CompanyCity THEN 1 ELSE 0 END) AS exact_match,
                   SUM(CASE WHEN r.Supplier <> d.CompanyCity THEN 1 ELSE 0 END) AS mismatch
            FROM (SELECT DISTINCT PurchaseSupplierID, Supplier FROM dbo.vwReceiverLog) r
            JOIN dbo.udfCompanyRetrieveDisplayNames(1) d ON d.CompanyID = r.PurchaseSupplierID
            WHERE r.Supplier IS NOT NULL
            """)
        run(cur, "C4. what status suffixes exist?",
            """
            SELECT status_suffix, COUNT(*) AS companies FROM (
              SELECT CASE WHEN CHARINDEX(' (', CompanyCity) > 0
                          THEN SUBSTRING(CompanyCity, CHARINDEX(' (', CompanyCity), 40)
                          ELSE '(none)' END AS status_suffix
              FROM dbo.udfCompanyRetrieveDisplayNames(1)) x
            GROUP BY status_suffix ORDER BY companies DESC
            """, cap=20)

        # ══════════════════════════════════════════════════════════════════
        rule("D. VENDOR EXTRACT — 'CActive AND has address' is 5 rows away from 1,803")

        print("\n  The extract had 1,803 rows with 58 incomplete (missing name, address")
        print("  or postcode). If the population already requires an address, the 58")
        print("  should be almost entirely missing postcodes. That is the check.\n")

        run(cur, "D1. fingerprint the vendor candidates: size AND incompleteness",
            """
            SELECT 'CActive and has address' AS scope, COUNT(*) AS companies,
                   SUM(CASE WHEN CZip IS NULL OR LTRIM(RTRIM(CZip)) = '' THEN 1 ELSE 0 END) AS no_postcode
              FROM dbo.tblCompany
              WHERE CActive = 1 AND CAddress1 IS NOT NULL AND LTRIM(RTRIM(CAddress1)) <> ''
            UNION ALL
            SELECT 'has address (any status)', COUNT(*),
                   SUM(CASE WHEN CZip IS NULL OR LTRIM(RTRIM(CZip)) = '' THEN 1 ELSE 0 END)
              FROM dbo.tblCompany
              WHERE CAddress1 IS NOT NULL AND LTRIM(RTRIM(CAddress1)) <> ''
            UNION ALL
            SELECT 'CActive only', COUNT(*),
                   SUM(CASE WHEN CZip IS NULL OR LTRIM(RTRIM(CZip)) = '' THEN 1 ELSE 0 END)
              FROM dbo.tblCompany WHERE CActive = 1
            UNION ALL
            SELECT 'suppliers only', COUNT(*),
                   SUM(CASE WHEN CZip IS NULL OR LTRIM(RTRIM(CZip)) = '' THEN 1 ELSE 0 END)
              FROM dbo.tblCompany co
              WHERE EXISTS (SELECT 1 FROM dbo.tblSupplier s WHERE s.CompanyID = co.CompanyID)
            """)
        run(cur, "D2. exact duplicates on (name, address, postcode) — the extract found 8",
            """
            SELECT COUNT(*) AS companies_in_a_duplicate_group FROM (
              SELECT UPPER(LTRIM(RTRIM(CName))) AS n, UPPER(LTRIM(RTRIM(CAddress1))) AS a,
                     UPPER(LTRIM(RTRIM(CZip))) AS z, COUNT(*) AS c
              FROM dbo.tblCompany
              WHERE CActive = 1 AND CAddress1 IS NOT NULL AND LTRIM(RTRIM(CAddress1)) <> ''
                AND CZip IS NOT NULL AND LTRIM(RTRIM(CZip)) <> ''
              GROUP BY UPPER(LTRIM(RTRIM(CName))), UPPER(LTRIM(RTRIM(CAddress1))),
                       UPPER(LTRIM(RTRIM(CZip)))
              HAVING COUNT(*) > 1) g
            """)

        rule("PROBE 4 COMPLETE — paste everything above")

    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
