"""
eto_year_probe.py — probe 5, and the last one: is the extract a recent time slice?

    python tools/eto_year_probe.py --sql-auth

READ-ONLY. Paste the whole output back.

Four probes in, the PO extract still is not reproducible:

    probe 3   no SCOPE matches      every candidate is 45-49% on-time; extract is 65.2%
    probe 4   no DATE BASIS matches  best of nine variants is 53.0%

Both held one thing constant that the extract does not share: **time**. The NCR
analysis dated the extract to roughly May 2025, and every probe so far has measured
the population as it stands today, pooled across 2018-2026. If delivery performance
improved materially over those years, a recent slice would sit well above the pooled
average — and 65.2% would stop being impossible.

That is the last untested dimension. Section A measures on-time by PO year, which no
probe has done. Section B varies the one input never varied: which column decides a
line is fully received.

If neither explains it, the extract is not reproducible and the honest move is to
stop: adopt ETO as the baseline with a deliberately chosen scope, and reconcile on
ratios and structure rather than on matching a number whose provenance is lost.
That decision is already written up in docs/ETO_MAPPING.md section 11.4.

Target signature, for reference:
    lines 23,344 | eligible 20,181 | on-time 13,164 (65.2%) | vendors 416
"""

import os
import sys

SERVER = os.environ.get("ETO_SERVER", r"MACRO-ETO-SVR\SQLEXPRESS")
DATABASE = os.environ.get("ETO_DATABASE", "Macrodyne_Production")
DRIVER = os.environ.get("ETO_DRIVER", "ODBC Driver 17 for SQL Server")


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


def run(cur, label, sql, cap=40):
    print("\n" + "-" * 78 + f"\n{label}\n" + "-" * 78)
    try:
        cur.execute(sql)
        while cur.description is None and cur.nextset():
            pass
        if cur.description is None:
            print("  (no result set)")
            return
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print("  " + " | ".join(cols))
        for r in rows[:cap]:
            print("  " + " | ".join("" if v is None else str(v)[:50] for v in r))
        if len(rows) > cap:
            print(f"  ... (+{len(rows) - cap} more)")
        if not rows:
            print("  (0 rows)")
    except Exception as exc:
        print(f"  [ERROR] {type(exc).__name__}: {exc}")


NEED_BY = "COALESCE(pod.DateRevised, pod.DateRequired)"
JOINS = """
    FROM dbo.vwPurchaseOrderDetails pod
    JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
    LEFT JOIN dbo.vwReceiverLogSummed rls ON rls.PurchaseDetailID = pod.PurchaseDetailID
"""
ELIGIBLE = (f"ISNULL(rls.SumOfQtyReceived, 0) >= pod.PurchaseQty AND pod.PurchaseQty > 0"
            f" AND {NEED_BY} IS NOT NULL AND rls.MaxOfDate IS NOT NULL")


def main():
    conn = connect("--sql-auth" in sys.argv)
    cur = conn.cursor()

    try:
        rule("A. ON-TIME BY PO YEAR — the dimension no probe has measured")

        print("\n  If delivery performance improved over the years, a recent slice sits")
        print("  above the pooled 49%. The extract dates to about May 2025.\n")

        run(cur, "A1. per year", f"""
            SELECT YEAR(poh.PurchaseDate) AS po_year,
                   COUNT(*) AS lines,
                   SUM(CASE WHEN {ELIGIBLE} THEN 1 ELSE 0 END) AS eligible,
                   SUM(CASE WHEN ({ELIGIBLE}) AND rls.MaxOfDate <= {NEED_BY}
                            THEN 1 ELSE 0 END) AS on_time,
                   COUNT(DISTINCT poh.PurchaseSupplierID) AS vendors
            {JOINS}
            GROUP BY YEAR(poh.PurchaseDate)
            ORDER BY po_year DESC
            """, cap=20)

        print("\n  Compute on-time% per year from A1 by hand: on_time / eligible.")
        print("  Any year at ~65% is the answer; if the best year is ~55%, time is not")
        print("  the explanation either and the hunt ends here.\n")

        run(cur, "A2. windows ending 2025-05-31 (the extract's estimated vintage)", f"""
            SELECT w.label, COUNT(*) AS lines,
                   SUM(CASE WHEN {ELIGIBLE} THEN 1 ELSE 0 END) AS eligible,
                   SUM(CASE WHEN ({ELIGIBLE}) AND rls.MaxOfDate <= {NEED_BY}
                            THEN 1 ELSE 0 END) AS on_time,
                   COUNT(DISTINCT poh.PurchaseSupplierID) AS vendors
            FROM (VALUES ('12 months to 2025-05', '2024-06-01'),
                         ('18 months to 2025-05', '2023-12-01'),
                         ('24 months to 2025-05', '2023-06-01'),
                         ('36 months to 2025-05', '2022-06-01')) w(label, start_date)
            CROSS JOIN dbo.vwPurchaseOrderDetails pod
            JOIN dbo.vwPurchaseOrderHeader poh ON poh.PurchaseOrderID = pod.PurchaseOrderID
            LEFT JOIN dbo.vwReceiverLogSummed rls ON rls.PurchaseDetailID = pod.PurchaseDetailID
            WHERE poh.PurchaseDate >= CAST(w.start_date AS datetime)
              AND poh.PurchaseDate <  CAST('2025-06-01' AS datetime)
            GROUP BY w.label
            ORDER BY w.label
            """, cap=20)

        # ══════════════════════════════════════════════════════════════════
        rule("B. FULLY-RECEIVED SOURCE — the one input never varied")

        print("\n  Every probe so far decided 'fully received' from the receiver log.")
        print("  The PO line carries its own running quantity, pod.Received, and the")
        print("  two need not agree.\n")

        run(cur, "B1. do the two receipt quantities agree?", """
            SELECT COUNT(*) AS lines,
                   SUM(CASE WHEN pod.Received = ISNULL(rls.SumOfQtyReceived, 0)
                            THEN 1 ELSE 0 END) AS agree,
                   SUM(CASE WHEN pod.Received > ISNULL(rls.SumOfQtyReceived, 0)
                            THEN 1 ELSE 0 END) AS detail_higher,
                   SUM(CASE WHEN pod.Received < ISNULL(rls.SumOfQtyReceived, 0)
                            THEN 1 ELSE 0 END) AS log_higher
            FROM dbo.vwPurchaseOrderDetails pod
            LEFT JOIN dbo.vwReceiverLogSummed rls ON rls.PurchaseDetailID = pod.PurchaseDetailID
            """)

        run(cur, "B2. eligibility and on-time using pod.Received for fullness", f"""
            SELECT COUNT(*) AS lines,
                   SUM(CASE WHEN pod.Received >= pod.PurchaseQty AND pod.PurchaseQty > 0
                             AND {NEED_BY} IS NOT NULL AND rls.MaxOfDate IS NOT NULL
                            THEN 1 ELSE 0 END) AS eligible,
                   SUM(CASE WHEN pod.Received >= pod.PurchaseQty AND pod.PurchaseQty > 0
                             AND {NEED_BY} IS NOT NULL AND rls.MaxOfDate IS NOT NULL
                             AND rls.MaxOfDate <= {NEED_BY}
                            THEN 1 ELSE 0 END) AS on_time
            {JOINS}
            """)

        run(cur, "B3. and with the header need-by, in case both differ together", f"""
            SELECT COUNT(*) AS lines,
                   SUM(CASE WHEN pod.Received >= pod.PurchaseQty AND pod.PurchaseQty > 0
                             AND COALESCE(poh.PurchaseDateRevised, poh.PurchaseDateRequired) IS NOT NULL
                             AND rls.MaxOfDate IS NOT NULL
                            THEN 1 ELSE 0 END) AS eligible,
                   SUM(CASE WHEN pod.Received >= pod.PurchaseQty AND pod.PurchaseQty > 0
                             AND COALESCE(poh.PurchaseDateRevised, poh.PurchaseDateRequired) IS NOT NULL
                             AND rls.MaxOfDate IS NOT NULL
                             AND rls.MaxOfDate <= COALESCE(poh.PurchaseDateRevised, poh.PurchaseDateRequired)
                            THEN 1 ELSE 0 END) AS on_time
            {JOINS}
            """)

        rule("PROBE 5 COMPLETE — paste everything above")

        print("\n  Reading it: if no year and no window in section A reaches ~65% on-time")
        print("  and ~416 vendors, and section B does not move it either, then the")
        print("  extract cannot be reproduced from ETO. That is a finding, not a")
        print("  failure -- see docs/ETO_MAPPING.md section 11.4 for what follows.\n")

    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
