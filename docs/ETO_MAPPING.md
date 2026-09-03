# Vendor Scorecard — ETO Database Mapping

**Companion to:** `docs/DESIGN.md` (as-built specification and target-state design)
**Source system:** Total ETO — `Macrodyne_Production` on `MACRO-ETO-SVR\SQLEXPRESS`
**Access:** `TotalETOReportWriter`, read-only
**Schema authority:** `ETO_SCHEMA_MAP.md` (verified 2026-07-04 against declared constraints and live join tests)
**Document date:** 2026-09-03

---

## Table of Contents

1. [Scope and Decisions](#1-scope-and-decisions)
2. [Where the Data Lives](#2-where-the-data-lives)
3. [Entity Map](#3-entity-map)
4. [Supplier](#4-supplier)
5. [Project](#5-project)
6. [Order — Purchase Orders](#6-order--purchase-orders)
7. [Non-Conformances](#7-non-conformances)
8. [Item Master](#8-item-master)
9. [What the Database Changes](#9-what-the-database-changes)
10. [What the Database Does Not Fix](#10-what-the-database-does-not-fix)
11. [The Scope Question](#11-the-scope-question)
12. [Code Architecture](#12-code-architecture)
13. [Migration Procedure](#13-migration-procedure)
14. [Open Verification Items](#14-open-verification-items)
15. [Risks](#15-risks)

---

## 1. Scope and Decisions

### 1.1 What this covers

Replacing the Vendor Scorecard's Excel input layer with read-only queries against ETO, for the four datasets the pipeline consumes — supplier, order, non-conformance and item — plus **project**, which the Excel prototype mapped but never used.

### 1.2 Decisions taken

| Decision | Choice | Consequence |
|---|---|---|
| **Fidelity** | Parity first, then improve | The SQL returns the same column contract, so `main.py` and every metric module run unchanged and a SQL run can be reconciled against an Excel run row-for-row. Metric improvements land afterwards, as their own reviewable change. |
| **Project** | Attribute and optional filter | Scorecard grain stays at supplier level. `ProjectID` and the project name ride on every PO and NCR row, so a run can be scoped to selected projects — the same pattern the Project Console already uses. |
| **Vendor identity** | Both paths returned | `vendor_name` (the parsed display name) keeps parity today; `supplier_company_id` (the real key) ships alongside it so the switch is a second, verifiable step rather than a leap. |
| **Receipt basis** | ETO's own | On-time delivery uses `vwReceiverLogSummed`, the basis `dbo.urpPurchasingLateVendors` uses. The PO line's own `Received` / `LastReceivedDate` are returned too, so the two can be reconciled instead of argued about. |

**One stated parity exception.** A PO line with no receiver-log row has a NULL receipt quantity in SQL. Whether the Excel export carried `0` or a blank for the same line is not established anywhere, so the repository fills it to `0` as a deliberate choice rather than a reproduction. Left NULL, `received_qty >= ordered_qty` is false on every line, `fully_received` is false everywhere, and On-Time Delivery scores nothing at all. Treating "no receipt recorded" as zero received is the only reading that keeps the component alive, and it is the reading ETO's own late report takes: `ISNULL(SumOfQtyReceived, 0)`.

### 1.3 Why parity first

The prototype's numbers are the only baseline that exists. Until a SQL run reproduces them, any difference is ambiguous: a migration bug and a metric improvement look identical in a spreadsheet. Parity converts that ambiguity into a number — `tools/reconcile_sources.py` prints it.

This is also why **`ExcelRepository` is left untouched** by this work. Changing the thing you are reconciling against defeats the reconciliation.

---

## 2. Where the Data Lives

### 2.1 Governance

Two rules are in play, and they are not the same rule.

`REPORTING_SCHEMA_FRAMEWORK.md` is the strict one: *"Never directly query `dbo` in production reports — stage through the Reporting schema."* `ETO_REPORTING_SUITE_MAP.md` states the operating practice: production reports read ETO's **views** (which pre-resolve the joins) or IT-owned Reporting-schema cache tables, never `dbo` base tables. `dbo` is vendor-owned and IT-read-only.

**These queries meet the second rule, not the first.** They read `dbo` views directly, exactly as the Project Console does, and four objects are read as base tables because no view is known to exist for them. Under the framework's own rule the whole thing is non-conformant, not just those four — staging through a Reporting-schema view or cache table is the conformant end state, and it belongs with the persistence work in `docs/DESIGN.md` §15.3 rather than with this migration.

The immediate implication is smaller: prefer a view wherever one exists, and treat the four base-table reads as an open question (V-13), because a view may exist that nobody has looked for.

### 2.2 Objects used

| Purpose | Object | Type | Basis |
|---|---|---|---|
| PO lines (driver) | `dbo.vwPurchaseOrderDetails` | view | verified |
| PO headers | `dbo.vwPurchaseOrderHeader` | view | verified |
| Receipts per line | `dbo.vwReceiverLogSummed` | view | verified — `PurchaseDetailID`, `SumOfQtyReceived`, `MaxOfDate` |
| Receipt events | `dbo.vwReceiverLog` / `tblReceiverLog` | view/table | verified as "receipt events per line, with dates"; column list not yet dumped |
| NCRs (driver) | `dbo.vwNonConformances` | view | verified |
| NCR task counts | `dbo.vwNonConformanceList` | view | verified — `Tasks`, `Outstanding` |
| NCR costs | `dbo.vwCostingSummed_ByNC` | view | verified |
| NCR origin dept | `dbo.tlkpNonConformanceOrigin` | lookup | verified |
| Companies | `dbo.tblCompany` | table | verified — no view known |
| Supplier extension | `dbo.tblSupplier` | table | verified — no view known |
| Item master | `dbo.tblEngItemMaster` | table | verified — no view known |
| On-hand | `dbo.vwInventory` | view | verified |
| Projects | `dbo.tblProjects` | table | verified — no view known |

### 2.3 Connection

`pyodbc`, ODBC Driver 17, Windows auth by default, SQL auth via `ETO_USER` / `ETO_PWD` environment variables. Identical to the pattern `console_store.eto_connection()` already uses, so the two products share one proven connector shape.

The connection is opened `autocommit=True` and only ever issues `SELECT`. **This pipeline never writes to ETO.**

---

## 3. Entity Map

*Row counts below come from `ETO_SCHEMA_MAP.md` (base tables, 2026-07-04) and have since grown — later probes recorded ~160,803 PO lines, ~38,258–38,415 headers and ~1,872 NCRs. They are shown for shape, not as current figures, and the probe re-counts every one.*

```
                          tblCompany  (~2,052)
                          CompanyID PK
                          CName  ← dual-purpose: customer AND supplier name
                             │
              ┌──────────────┼──────────────────────────┐
              │ as customer  │ as supplier               │ extension
              ▼              ▼                           ▼
        tblProjects   vwPurchaseOrderHeader (~37,971)  tblSupplier
        ProjectID PK  PurchaseOrderID PK              CompanyID PK
        CompanyID FK  PurchaseSupplierID FK ──────────┘ (terms, FOB, QA-approved)
        DisplayName   PurchaseDate, PurchaseCurr,
        PManagerID    PurchaseCurrRate, Printed/Emailed
              │              │ 1:N
              │              ▼
              │       vwPurchaseOrderDetails (~159,199)   ← SCORECARD PO DRIVER
              │       PurchaseDetailID PK
              └───────ProjectID, SpecID  (composite → tblSpec)
                      ItemID FK ──────────────► tblEngItemMaster
                      PurchaseQty, PurchasePrice, ExtendedPrice
                      DateRequired, DateRevised, Received, Archived
                             │ 1:1
                             ▼
                      vwReceiverLogSummed
                      PurchaseDetailID, SumOfQtyReceived, MaxOfDate

        vwNonConformances (~1,847)                        ← SCORECARD NCR DRIVER
        NonConformanceID PK
        ProjectID          NOT NULL, soft link
        PurchaseOrderID    NULLABLE — 70% NULL, LEFT JOIN ONLY
        Supplier           decorated display name
        Resolved (bit), CreationDate, Released
             │ 1:1                    │ 1:1
             ▼                        ▼
        vwNonConformanceList   vwCostingSummed_ByNC
        Tasks, Outstanding     TotalNCCostingValue
```

**The load-bearing fact:** `vwPurchaseOrderHeader.PurchaseSupplierID → tblCompany.CompanyID` is a **declared foreign key**. Supplier identity in ETO is an integer key, not a name. Everything the scorecard currently does with regular expressions is reconstructing, imperfectly, a relationship the database already states exactly.

---

## 4. Supplier

### 4.1 The finding that matters

`tblCompany.CName` carries the `NAME [CITY] (Approved)` convention — the Project Console's `ncspec._clean_client()` strips exactly that shape (`"Bosch Rexroth [Concord] (Approved)"` → `"Bosch Rexroth"`), and the scorecard's `normalize_vendor_name()` / `extract_vendor_city()` parse the same string.

So the Excel `Supplier` column **is** `CName`, and the scorecard's whole vendor-identity mechanism is a regex reimplementation of a foreign key. `PurchaseSupplierID` was on every PO row the entire time.

This resolves three of the design document's defects at once:

- **D-01** (identity from free text) — the key exists and is declared.
- **D-17** (bracket-position sensitivity) — no parsing, no sensitivity.
- Split vendor rows caused by name spelling variants — one `CompanyID`, one row.

One caveat worth stating plainly: the Excel `Supplier #` column is **not** this key. Caption discovery on 2026-08-14 showed `PurchaseOrderDetailCustom3` is captioned "Supplier #" — a line-level custom number field, unrelated to `CompanyID`. Do not substitute one for the other.

### 4.2 Mapping — `get_vendors()`

Driver: `dbo.tblCompany`, scoped to suppliers.

| Contract column | ETO expression | Confidence |
|---|---|---|
| `company_id` | `co.CompanyID` | verified |
| `vendor_name` | `co.CName` | verified |
| `address_line_1` | `co.CAddress1` | likely |
| `address_line_2` | `co.CAddress2` | likely |
| `city` | `co.CCity` | likely |
| `state_province` | `co.CState` | likely |
| `postal_code` | `co.CZip` | likely |
| `country` | `co.CCountry` | likely |

The address columns are "likely" rather than verified because the schema map does not enumerate `tblCompany`'s columns — but the Excel mapping file names exactly `CompanyID`, `CName`, `CAddress1`, `CAddress2`, `CCity`, `CState`, `CZip`, `CCountry`, which is only explicable if the vendor extract came from this table.

One piece of contrary evidence: ETO's own `urpNonConformanceSupplierImpact` selects `CompanyCity` through the display UDF `udfCompanyRetrieveDisplayNames(1)` — so ETO's supplier-display path uses a differently named city column. That does not disprove `CCity` on the base table, but the inference is not airtight. The probe settles it.

`tblCompany.CActive` and `tblSupplier.SupNetTerms` / `SupQAApproved` are confirmed in `ETO_ERD.mermaid` and are returned as additive columns.

### 4.3 Supplier scope

`tblCompany` is dual-purpose: the same table holds customers and suppliers, distinguished only by which foreign key you arrive through. A scope filter is therefore **mandatory** — without one, `get_vendors()` returns customers and the Vendor Review sheet fills with irrelevant records.

Three scopes are configurable via `options.vendor_scope`:

| Scope | Definition | Use |
|---|---|---|
| `supplier_table` | has a row in `tblSupplier` | default — ETO's own definition of "is a supplier" |
| `active_suppliers` | as above, and `co.CActive = 1` | a ~12% trim by an active flag explains 1,803 at least as naturally as supplier membership does |
| `purchased_from` | appears as `PurchaseSupplierID` on any PO | narrower; only vendors with trading history |
| `all_companies` | no filter | diagnostic only |

The Excel extract held 1,803 rows against ~2,052 companies. Which scope reproduces 1,803 is a reconciliation question, and probe section G counts all four.

---

## 5. Project

### 5.1 Role

Supplier remains the scorecard grain. Project arrives as an **attribute and an optional filter**:

- Every PO line carries `project_number` (already in the column contract, previously unused) and, additively, `project_name`.
- Every NCR carries `project_number`.
- `scope.project_ids` in `config/eto.json` restricts a whole run to selected projects. Empty list means all projects.

This mirrors the Project Console's model, where the left rail picks projects and every report scopes to them — so a scorecard run and a console report can be made to cover the same population, which is what makes the two comparable.

### 5.2 Mapping

| Field | ETO expression | Notes |
|---|---|---|
| `project_number` | `pod.ProjectID` / `nc.ProjectID` | `ProjectID` is the business key and the PK. **Not** `ProjectAutoID`, which is a separate surrogate. |
| `project_name` | `pj.DisplayName` | additive; joined `LEFT` from `tblProjects` |
| customer | `tblProjects.CompanyID → tblCompany.CName` | available, not currently returned |

### 5.3 Two cautions

**`tblSpec` needs a composite join.** `machine_code` maps to `pod.SpecID`, and `SpecID` is unique only *within* a project — `SpecID = 10` appears in 130,323 timecards across many projects. The scorecard only carries `SpecID` as a label, so no join is performed. Any future work that resolves a machine name **must** join on `(ProjectID, SpecID)`.

**`tblProjects` schedule fields are abandoned.** Portfolio-wide across all 1,777 projects: `PDelivery` populated = 0, `PercentComplete` = 0, `PActive = 1` = 0. Do not filter on `PActive` and do not expect project dates from this table.

---

## 6. Order — Purchase Orders

### 6.1 Grain

One row per PO detail line — `PurchaseDetailID` — matching the Excel extract exactly. `vwPurchaseOrderDetails` is the driver, `vwPurchaseOrderHeader` joins `N:1`, `vwReceiverLogSummed` joins `1:1`, `tblProjects` joins `N:1`. No join in the query can multiply the driver's rows.

### 6.2 Mapping — `get_purchase_orders()`

| Contract column | ETO expression | Confidence | Note |
|---|---|---|---|
| `po_number` | `poh.PurchaseOrderID` | verified | the console surfaces this as "PO #" |
| `project_number` | `pod.ProjectID` | verified | |
| `machine_code` | `pod.SpecID` | verified | label only; not project-unique |
| `ordered_qty` | `pod.PurchaseQty` | verified | |
| `part_number` | `im.ItemCompanyID` via `pod.ItemID` | probe (item master) | the PO line has `ItemID`/`ItemDescription` but **no part number** — see §6.5 |
| `po_part_number` | — | **probe** | supplier's part number; may not exist on the line |
| `vendor_name` | `poh.CName` | verified | the decorated string the pipeline parses |
| `unit_price` | `pod.PurchasePrice` | verified | |
| `required_date` | `pod.DateRequired` | verified | **detail**, not header — ETO's own late report uses the detail dates and they diverge |
| `revised_date` | `pod.DateRevised` | verified | as above |
| `last_receipt_date` | `rls.MaxOfDate` | verified | configurable; see §6.3 |
| `uom` | `pod.PurchaseUOM` | verified | load-bearing for Commercial — see §6.4 |
| `received_qty` | `rls.SumOfQtyReceived` | verified | configurable; NULL filled to 0 |
| `order_date` | `poh.PurchaseDate` | verified | also the created/placed date — ETO keeps no separate entry timestamp |
| `extended_value` | `pod.ExtendedPrice` | verified | pre-computed by the view |
| `currency_code` | `poh.PurchaseCurr` | verified | |
| `currency_rate` | `poh.PurchaseCurrRate` | verified | mapped but unused today; the FX-normalisation hook |
| `supplier_number` | `pod.PurchaseOrderDetailCustom3` | verified | caption = "Supplier #". **Not** the company key |
| `order_number` | — | **probe** | ambiguous in the Excel extract |
| `receiving_date` | `pod.PurchaseOrderDetailCustom5` | verified | caption = "Receiving Date"; populated on <1% of lines |

**Additive** (returned, not consumed): `supplier_company_id`, `purchase_detail_id`, `project_name`, `detail_received_qty`, `detail_last_receipt`, `log_received_qty`, `log_last_receipt`.

### 6.5 Part number comes from the item master, not the PO line

`vwPurchaseOrderDetails` carries `ItemID` and `ItemDescription`. It does **not** carry a part number — the verified column inventory of that view names neither `ItemCompanyID` nor `PartNumber`. So `part_number` is resolved through the declared FK `pod.ItemID → tblEngItemMaster.ItemID`, using the *same expression* as the items dataset.

Two reasons this is the right shape rather than a workaround:

1. **The two datasets cannot disagree.** Lead-Time matching normalises the PO's `part_number` and the Item Master's `part_number` into a common key and joins them. Sourcing both from one expression makes a mismatch structurally impossible — today's 99.97% match rate becomes 100% by construction.
2. **It is the same argument the document makes for vendors.** ETO identifies an item by `ItemID`, a declared key; the part number is a display attribute hanging off it. Joining on the key and reading the label is what the supplier section argues for, applied to items.

`item_id` and `item_description` also ship as additive columns, so the key itself is available when the pipeline is ready to use it.

### 6.3 Receipt basis — two sources, both returned

ETO exposes fulfilment twice, and the two do not have to agree:

| Source | Columns | Nature |
|---|---|---|
| PO line | `pod.Received`, `pod.LastReceivedDate` | the line's own running state |
| Receiver log | `rls.SumOfQtyReceived`, `rls.MaxOfDate` | derived from actual receipt transactions |

**The receiver log is authoritative.** ETO's own vendor-delivery report `dbo.urpPurchasingLateVendors` defines lateness as:

```
need-by  = ISNULL(POD.DateRevised, POD.DateRequired)
receipt  = vwReceiverLogSummed.MaxOfDate
DaysLate = DATEDIFF(d, need-by, receipt) > 0
fully received: (PurchaseQty - ISNULL(SumOfQtyReceived, 0)) <= 0
```

A live comparison on project 230219 matched this arithmetic **340 of 340 lines, zero mismatches**.

That matters for the scorecard, but the claim has to be stated carefully. `delivery_evaluator.prepare_delivery_metrics()` computes the target date as revised-else-required, gates on fully-received, and calls a line late when the receipt date exceeds the target — so the prototype's rule is **equivalent in shape** to ETO's. It is not identical, in two ways:

| | ETO | Prototype |
|---|---|---|
| Granularity | `DATEDIFF(d, need-by, receipt) > 0` — whole days | `last_receipt_date > target_date` — full timestamp |
| Fully received | `PurchaseQty − ISNULL(SumOfQtyReceived,0) <= 0` | `ordered_qty > 0 AND received_qty >= ordered_qty` |

A receipt at 14:00 on the need-by date is **on-time in ETO and late in the scorecard**, and `MaxOfDate` is a datetime, so this is not hypothetical. A zero-quantity line is fully received in ETO and ineligible in the prototype.

Note also what the 340/340 actually validated: the Project Console's **SQL** transcription against the stored procedure. It never touched the scorecard's pandas code. So the right conclusion is that the scorecard's target-date rule agrees with ETO's, and that open business decision #4 has a strong precedent — not that it is settled. Closing it means adopting day granularity, or deciding the timestamp comparison is what Macrodyne wants.

Both sources ship as additive columns so the difference between them can be measured rather than assumed. `options.last_receipt_source` and `options.received_qty_source` switch which one feeds the contract.

### 6.4 UOM is load-bearing

`commercial_evaluator` scopes every price comparison to `vendor + location + part + currency + UOM`, and `commercial_base_eligible` requires a non-null UOM. **If `uom` comes back NULL, the Commercial component scores nothing for every vendor** — a 20% weight silently renormalized away, and the overall score computed from fewer components. Confirmed empirically: an all-NULL column becomes all-`<NA>` under `astype("string")`, so the eligibility predicate is false on every row.

The column is `pod.PurchaseUOM`, named in the 2026-07-25 view discovery alongside `PurchaseQty`, `PurchaseCurr`, `PurchaseCurrRate` and `Received`. So this is a *populated-ness* question, not a naming question — probe section C2 counts it, because a column that exists but is empty is not a solution.

`EtoRepository.blocking_gaps()` guards it regardless, along with the five PO required fields and the NCR quantities (§12.4).

---

## 7. Non-Conformances

### 7.1 The supplier attribution finding

ETO gives two paths from an NCR to a supplier, and the scorecard currently uses the weaker one.

```
Path A (today)    nc.Supplier  ── parse "NAME [CITY]" ──►  match against parsed PO names
                  51 of 378 supplier-linked NCRs fail to match and are held as exceptions

Path B (ETO's)    nc.PurchaseOrderID ──► poh.PurchaseSupplierID ──► tblCompany.CompanyID
                  an integer key, no parsing, no ambiguity
```

Path B is exact where it applies — but `PurchaseOrderID` is **NULL on ~70% of NCRs** (1,298 of 1,847, verified). That is not a defect in the mapping; most NCRs are internal and have no supplier at all. What matters is the overlap: how many of the 378 *supplier-linked* NCRs also carry a `PurchaseOrderID`. Probe section E measures precisely that, and section E2 lists the cases where the NCR's own `Supplier` disagrees with the PO's — the most informative rows in the whole probe.

Both paths ship. `vendor_name` keeps parity; `supplier_company_id` is the migration target.

### 7.2 Mapping — `get_ncrs()`

Driver `dbo.vwNonConformances`, filtered `SActive = 1`.

| Contract column | ETO expression | Confidence |
|---|---|---|
| `ncr_number` | `nc.NonConformanceBarcode` | verified |
| `project_number` | `nc.ProjectID` | verified |
| `machine_code` | `nc.SpecID` | verified — float, NOT NULL |
| `title` | `nc.Title` | verified |
| `origin` | `nc.NonConformanceOriginDescription` | verified |
| `total_tasks` | `ncl.Tasks` | verified |
| `outstanding_tasks` | `ncl.Outstanding` | verified |
| `resolved` | `nc.Resolved` | verified — **bit**, see §7.3 |
| `released` | `nc.Released` | verified — the close date |
| `source_info` | `nc.SourceDescription` | verified |
| `po_number` | `nc.PurchaseOrderID` | verified — nullable |
| `part_number` | `nc.PartNumber` | verified |
| `quantity` | `nc.Quantity` | verified |
| `quantity_rejected` | `nc.QuantityRejected` | verified |
| `interim_action` | `nc.RecommendedInterim` | verified |
| `root_cause` | `nc.NonConformanceRootCause` | verified |
| `corrective_pre_action` | `nc.NonConformanceCorrectivePreventiveAction` | verified |
| `ncr_costs` | `ncc.TotalNCCostingValue` | verified |
| `ncr_hours` | — | probe — not consumed today |
| `target_date` | `nc.NonConformanceCustom5` | verified — caption = "Target Date" |
| `date_follow_up` | `nc.QualityFollowUp` | verified |
| `created_date` | `nc.CreationDate` | verified |
| `vendor_name` | `nc.Supplier` | verified |
| `item_id` | `nc.ItemID` | verified |

**Additive:** `supplier_company_id`, `po_supplier_name`, `ncr_supplier_id`, `nc_id`, `origin_department`.

`vwNonConformances` is documented as "header + resolved names", so the `tblNonConformance` header columns — `Quantity`, `QuantityRejected`, `SpecID`, `ItemID`, `RecommendedInterim`, `QualityFollowUp` — are addressable on the view. Six fields that looked like open questions were already answered by the 2026-07-26 NCR discovery.

`quantity` and `quantity_rejected` stay in `blocking_gaps()` anyway: they are load-bearing for NCR Rejected %, and without them the quality-eligibility predicate is false for every row while the Quality *score* — NCR count over PO count — carries on producing numbers. A component that half-works is harder to notice than one that fails.

### 7.3 The truth-value contract

ETO stores resolution as a SQL Server `bit`. `ncr_evaluator` does:

```python
responsiveness_eligible = supplier_linked & resolved.notna()
resolved_flag           = responsiveness_eligible & resolved.eq(True)
```

Tested against every shape this column can arrive in:

| Value as read | `.eq(True)` / `.eq(False)` behave? | Why |
|---|---|---|
| Python `bool` (what pyodbc returns for `bit`) | yes | native |
| `int` 1 / 0 | yes | `1 == True` in Python |
| `Decimal(1)` / `Decimal(0)` | yes | numeric equality |
| **string `'1'` / `'0'`** | **no** | both comparisons false |
| **string `'Yes'` / `'No'`** | **no** | both comparisons false |

So the bit column itself is safe, and the earlier worry about integers was unfounded. The real exposure is **text**: if the column is ever reached through a view or expression that returns a string, `notna()` is satisfied while both equality tests fail, so every vendor reports 0% responsiveness against a healthy-looking eligibility count. Nothing raises. That is the same class of failure the design document recorded as D-06 for the Excel path, and it is the worst kind — a confident wrong answer.

`EtoRepository._coerce_boolean()` normalises bool, numeric and the common text tokens (`1/0`, `true/false`, `yes/no`, `y/n`, `t/f`, case- and whitespace-insensitive) to a nullable boolean, and **raises on anything it cannot map**, naming the offending values. A startup error is recoverable; a quietly dead component is not.

### 7.4 Responsiveness — improvable immediately

The design document's §15.4 noted that the NCR extract already carries the fields for a real responsiveness metric. The database confirms it, with names:

| Field | ETO source | Enables |
|---|---|---|
| `created_date` | `nc.CreationDate` | age of an open NCR |
| `released` | `nc.Released` | actual closure date → **days to close** |
| `target_date` | `nc.NonConformanceCustom5` | **on-time closure** vs commitment |
| `outstanding_tasks` | `ncl.Outstanding` | open corrective actions per NCR |
| `total_tasks` | `ncl.Tasks` | completion ratio |

`Released` is the one the current proxy is missing. Today the metric is a binary resolved/unresolved ratio that treats a two-day closure and a two-year closure identically. `CreationDate` → `Released` is a duration, and `Released` vs `Custom5` is an on-time rate. Neither needs a new source, a new view, or a business decision about where the data comes from — only a decision about which definition to adopt.

---

## 8. Item Master

### 8.1 Mapping — `get_items()`

Driver `dbo.tblEngItemMaster`, joined `1:1` to a **pre-grouped** on-hand subquery.

The pre-grouping is not optional. ETO inventory is a shared pool with one row per item × location (verified 2026-08-03, locations "Macrodyne 1", "Macrodyne 2 (Racco)", "TOC"). Joining `vwInventory` directly would multiply every item row by its location count.

| Contract column | ETO expression | Confidence |
|---|---|---|
| `part_number` | `im.ItemCompanyID` | probe |
| `description` | `im.ItemDescription` | probe |
| `lead_time` | `im.EstimatedLeadTime` | verified — **and empty**, see §10 |
| `quantity_on_hand` | `SUM(vwInventory.QtyOnHand)` per `ItemID` | likely |
| `uom`, `category`, `list_price`, `revision`, `lpp`, `preferred_supplier`, `supplier_part_number`, `last_supplier`, `manufacturer`, `manuf_part_number`, `quantity_reserved` | — | probe |

**Additive:** `item_id`.

Only `part_number` and `lead_time` are consumed by the pipeline today, so the long probe list is low-risk: the other columns exist in the contract, are returned as NULL, and nothing reads them. `tblEngItemMaster` is also where the maintained `PartCustom7`/`PartCustom8` flags live (long-lead and oversize) — real, maintained data, and a candidate if item risk ever enters the scorecard.

---

## 9. What the Database Changes

Moving to ETO is not just a plumbing change. Five of the design document's defects become fixable, and two become measurable for the first time.

| Design doc defect | What ETO provides | Status after migration |
|---|---|---|
| **D-01** vendor identity from free text | `PurchaseSupplierID` → `CompanyID`, a declared FK | **Fixable now.** Both keys ship; switching the grain is a config-and-groupby change. |
| **D-03** Quality numerator/denominator scope mismatch | NCR → PO → supplier exact key | **Fixable**, to the extent probe E shows `PurchaseOrderID` coverage on supplier-linked NCRs. |
| **D-17** bracket-position parsing bug | no parsing at all | **Gone** with D-01. |
| **D-05** no evaluation period | `poh.PurchaseDate` and `nc.CreationDate` are queryable date columns | **Fixable now.** `scope.po_date_from` / `po_date_to` exist; a rolling window is a config value, not a code change. |
| **D-06** unvalidated `resolved` contract | `bit` with a known type | **Fixed** — the repository normalises the value and raises on anything it cannot map, instead of letting a text value silently zero the component (§7.3). |
| **D-09** no run history | the `scorecard` schema in `docs/DESIGN.md` §15.3 | Unblocked — persistence has somewhere to live. |
| Responsiveness proxy | `Released`, `Custom5` target date, task counts | **Improvable now**, no new source needed. |
| Partial receipts excluded from OTD | `vwReceiverLog` event-level receipts | Probe F dumps the shape; the biggest available OTD improvement. |
| **D-15** duplicated business rules | — | **Unchanged.** ETO does not help here, and this migration adds a second copy of the PO type validation (§15). |
| Project blindness | `ProjectID` on PO and NCR | **Delivered** as attribute + filter. |
| Cross-currency | `PurchaseCurrRate` on the header | Available; needs an agreed FX rule (open decision #11). |

---

## 10. What the Database Does Not Fix

**Lead-Time is dead at source.** `tblEngItemMaster.EstimatedLeadTime` is empty on **every** item — verified 2026-07-25 during the Project Console's late-PO work, where it forced the "Ordered Late" and "Critical" exception flags to be dropped as permanently blank for the same reason.

The design document recorded D-04 as a coverage problem in the Excel extract (3 of 23,344 rows eligible). It is not an extract problem. The benchmark does not exist in ETO, so migrating changes nothing: the Lead-Time component will remain `INSUFFICIENT BENCHMARK DATA` for every vendor, and its 15% weight will keep renormalizing away.

Two honest options, both requiring a business decision (open decision #7):

1. **Drop the component** from the published weighting until a benchmark exists. A permanently-N/A component makes Weight Coverage % harder to read and implies evidence that is never coming.
2. **Change the benchmark** to something ETO actually holds — the PO's own need-by date (`DateRequired`) against the order date is a *promise-to-delivery* measure rather than a *quoted-lead-time* measure, and it is fully populated.

Option 2 is a different metric, not the same metric with better data. Worth saying out loud before anyone adopts it.

Probe section H re-confirms the emptiness rather than assuming a 2026-07 finding still holds.

---

## 11. The Scope Question

**This is the largest open item in the migration, and it is not a technical one.**

| Population | Rows |
|---|---|
| `vwPurchaseOrderDetails` | ~160,803 |
| The Excel extract the prototype scored | 23,344 **after cleaning** |

Two details that change how this is measured. First, 23,344 is a *post-cleaning* count — after header-row removal and the required-field split — while the probe counts raw rows, so the correct scope lands somewhat **above** 23,344, not on it. "Nearest 23,344" is the wrong test; "slightly above, and explicable" is right. Second, the scope the config ships with (`active_only`, `exclude_archived_lines`, `issued_only`) is a **placeholder, not a finding** — the not-sent backlog is only ~4.5% of headers, so those three filters land nowhere near the target. `scope.scope_confirmed` is `false` and the repository warns on every load until it is settled.

**And this is not only a PO problem.** All four extracts are unexplained:

| Dataset | Excel extract | ETO population |
|---|---|---|
| Purchase orders | 23,344 (post-cleaning) | ~160,803 lines |
| NCRs | 1,248 | ~1,872 (`SActive = 1` narrows it) |
| Item master | 86,730 | `tblEngItemMaster` — uncounted |
| Vendors | 1,803 | ~2,052 companies |

Probe section D covers POs, G covers vendors, and G4 counts the NCR and item populations that the first draft of this document overlooked. Every one of them will show as an unexplained diff in `reconcile_sources.py` until it is understood.

The extract was scoped by something — a project selection, a date window, an issued/active filter, a receipt condition, or a combination — and **nothing in the prototype, the README or the column mappings records what.** Every published figure in the design document (416 vendor rows, 106 scored, 20,181 delivery-eligible, the A/B/C/D distribution) describes that unidentified subset.

Two consequences:

1. **Reconciliation needs the scope first.** A SQL run against the wrong population produces a huge diff that says nothing about query correctness. Probe section D counts each candidate scope so the one that lands near 23,344 can be identified, then set in `config/eto.json`.

2. **The scope may not be defensible even once identified.** If the extract was, say, one buyer's saved filter or a single year, then the prototype's grades describe a slice nobody chose deliberately. That is worth knowing before any of those grades reach a supplier conversation.

If no scope reproduces 23,344, the honest conclusion is that the extract cannot be reconstructed, and the ETO run becomes the new baseline with its own explicitly chosen scope. That is a better outcome than a scope that merely happens to match a row count.

---

## 12. Code Architecture

### 12.1 New files

```
config/eto.json                        connection, scope, options, and every column expression
src/data_access/eto_queries.py         SQL construction + contracts + validation
src/data_access/sql_repository.py      EtoRepository(VendorScorecardRepository)
src/data_access/repository_factory.py  source selection + a closing context manager
tools/eto_schema_probe.py              read-only discovery (run on the ETO server)
tools/reconcile_sources.py             Excel vs ETO, stage totals + vendor diff
tools/dao_conformance_check.py         executable proof that the two DAOs are interchangeable
docs/ETO_MAPPING.md                    this document
```

**Unchanged:** `base_repository.py`, `ExcelRepository`, all four evaluators, both aggregators, the scoring engine, and the Excel exporter. The DAO layer itself is not modified — the factory is additive, and resource handling is duck-typed rather than pushed into the abstract class, so the Excel reconciliation baseline stays exactly as it was.

**`main.py` changed in two places**, both surgical: the import, and the five-line `ExcelRepository(...)` construction replaced by `create_repository()`. The default resolves to Excel with byte-identical arguments, so an unchanged environment behaves as it always did. That is deliberately the smallest possible edit to a 1,825-line module-level script with no `__main__` guard (`docs/DESIGN.md` D-10) — the Phase 0 stabilisation of that file is still worth doing, but it is no longer a prerequisite for reading from ETO.

### 12.2 Columns as configuration

Roughly a dozen columns cannot be named with confidence from the existing schema map. Rather than guess in SQL and edit code after each probe, **every projected column is a config entry**:

```json
"uom": null,
"part_number": "pod.ItemCompanyID"
```

`null` emits `CAST(NULL AS <type>) AS uom`, so the column contract holds and the pipeline runs while a field is unresolved. Correcting a name after the probe is a JSON edit and a re-run — no code change, no redeploy.

**That graceful degradation is not universal, and the exceptions are the dangerous ones.** Nulling one of the five PO required fields — `po_number`, `vendor_name`, `part_number`, `ordered_qty`, `order_date` — sends every row to `rejected_purchase_orders`, after which the type validation passes on an empty frame and the pipeline completes cheerfully with a zero-row scorecard. `EtoRepository.LOAD_BEARING` therefore covers the required set, `uom`, the two NCR quantities, and the item master's `part_number`; `check_ready()` raises rather than letting any of them through.

Two guards keep this from becoming an injection surface:

- Column expressions must match `^[A-Za-z0-9_.\[\]]+$` — bare `alias.Column` references only.
- Object names must match `^schema.object$`.

Scope **values** (project ids, dates) are never interpolated; they bind as pyodbc parameters.

### 12.3 Switching the source

```
python main.py                        Excel  (the default — unchanged behaviour)
python main.py --source=eto           read from ETO
SCORECARD_SOURCE=eto python main.py   same, via the environment
```

In code:

```python
from src.data_access.repository_factory import create_repository, repository

repo = create_repository()          # honours --source / SCORECARD_SOURCE
repo = create_repository("eto")     # explicit

with repository("eto") as repo:     # closes the connection on the way out
    items = repo.get_items()
```

Everything after that line is identical, whichever source is chosen. That is the repository abstraction paying for itself — the design document called it the codebase's most valuable decision, and this is the collection.

Selecting `eto` also runs `check_ready()`, which raises on an unresolved load-bearing column (naming it) and warns while the PO scope is unconfirmed. A run that would have scored nothing fails at startup instead.

### 12.5 Proving the swap

`tools/dao_conformance_check.py` is an executable proof rather than an assurance. It needs no database and no source workbooks: it writes throwaway `.xlsx` fixtures to a temp directory, feeds `ExcelRepository` from them, feeds `EtoRepository` the same logical rows in ETO-shaped form (Decimals, bit values, NULLs) through a stubbed driver, and compares. Seven sections: the DAO contract, method signatures, public surface, the column contract against `column_mappings.json`, frame-level parity, a full run through the real evaluators and scoring engine, and source selection.

Run against the repository as it stands: **43 checks, all passing, exit 0** on Python 3.10 / pandas 2.3.3. Two differences are reported as notes rather than failures:

- **`received_qty`** — the documented parity exception (§1.2). The values differ; every downstream consumer treats them identically, which section 6 verifies rather than assumes.
- **dtypes** — Excel infers a dtype per column, so an all-empty column lands as `float64` where SQL declares `nvarchar` or `datetime`. Values agree throughout, and the eight dtype-validated PO columns agree on dtype too.

The check also surfaced a fragility in the Excel path worth recording: **`ExcelRepository` rejects a source file in which a validated date column is entirely empty** — the column round-trips out of `.xlsx` as `object` dtype and fails `_validate_purchase_order_types`. `EtoRepository` cannot hit this, because even an unresolved column is emitted as `CAST(NULL AS datetime)` and carries the right dtype whether or not any row has a value. The ETO path is the more robust of the two.

### 12.4 Pre-flight

```python
blocking = repo.blocking_gaps()   # {'purchase_orders': ('uom',)} → refuse to score
report   = repo.preflight()       # everything still unresolved, load-bearing or not
```

A component that scores nothing because a column is NULL looks exactly like a component that scores nothing because a vendor has no history. `blocking_gaps()` is what separates the two before a run rather than after a meeting.

---

## 13. Migration Procedure

Ordered so each step is verifiable before the next depends on it.

**Step 1 — Probe.** Run `tools/eto_schema_probe.py` on a machine that reaches `MACRO-ETO-SVR`; paste the output. Read-only, no dependencies beyond `pyodbc`.

**Step 2 — Resolve columns.** Fill in `config/eto.json` from probe sections B and C. `uom`, `quantity` and `quantity_rejected` first — everything else degrades gracefully.

**Step 3 — Settle the scope.** From probe section D, find the filter combination nearest 23,344 and set `scope`. If nothing lands close, record that the extract is not reconstructable and choose a scope deliberately (§11).

**Step 4 — Reconcile.** `python tools/reconcile_sources.py`. Target: stage totals matching, or every difference explained. Expect the vendor diff to show rows only in one source — those are vendor-identity differences, and they are the D-01 evidence.

**Step 5 — Cut over.** Run `python main.py --source=eto`. The workbook should reproduce. Nothing needs editing: the Excel path stays the default, and it remains both the rollback and the regression baseline.

Run `python tools/dao_conformance_check.py` first — it takes seconds, needs no database, and fails loudly if anything in the DAO contract has drifted.

**Step 6 — Then improve.** In dependency order, each as its own reviewable change:

1. Evaluation period (`scope.po_date_from` / `po_date_to`) — D-05, and it makes every later comparison meaningful.
2. Vendor key → `supplier_company_id` — D-01, D-03, D-17.
3. Responsiveness → days-to-close and on-time-closure — §7.4.
4. Receipt-event OTD with partial receipts — probe F.
5. Quality evidence gate — D-02, unchanged by the migration and still the largest scoring distortion.
6. Persist runs to the `scorecard` schema — D-09.

**Steps 1–5 change no numbers by design. Step 6 changes numbers on purpose.** Keeping that line sharp is what lets anyone tell the two apart later.

---

## 14. Open Verification Items

Reduced substantially from the first draft: cross-checking against the project's own discovery documents resolved `uom`, both NCR quantities, `SpecID`, `ItemID`, `RecommendedInterim` and `QualityFollowUp`, all of which had been carried as unknowns. What remains, with the consequence of each.

| # | Item | Status | If unresolved |
|---|---|---|---|
| V-01 | **`im.ItemCompanyID` is the item number** | open | **The one genuinely load-bearing unknown.** It is the PO required field `part_number` *and* the lead-time match key, and it feeds both datasets. A wrong name errors loudly; a `null` produces a zero-row scorecard — which is why `LOAD_BEARING` refuses it |
| V-02 | **PO scope that yields ~23,344+** | open | Reconciliation is not interpretable (§11) |
| V-03 | **Vendor scope that yields 1,803** | open | Vendor Review population differs; `active_suppliers` is the new candidate |
| V-04 | **NCR and item scopes** | open | The other two unexplained extracts (1,248 NCRs, 86,730 items) — probe G4 |
| V-05 | `poh.PurchasePrinted` / `PurchaseEmailed` on the **view** | open | Verified on `tblPurchaseOrderHeader`, not on `vwPurchaseOrderHeader`. `issued_only` defaults on, so a missing column fails the entire PO query |
| V-06 | `PurchaseOrderID` coverage on supplier-linked NCRs | open | Cannot size the D-01/D-03 fix — probe E |
| V-07 | `vwReceiverLog` column shape | open | Partial-receipt OTD stays blocked. The object is confirmed to exist and to hold receipt events with dates; only its columns are undumped |
| V-08 | `tblCompany` address columns | open | Vendor completeness checks misfire. `CCity` has one piece of contrary evidence (§4.2) |
| V-09 | `pod.PurchaseUOM` **populated**, not just present | open | Name is verified; emptiness would still kill Commercial — probe C2 |
| V-10 | `EstimatedLeadTime` still empty | open | Determines whether D-04 is closed or merely re-confirmed |
| V-11 | Item master attribute columns | open | Return NULL; only `part_number` and `lead_time` are consumed, so low impact |
| V-12 | `po_part_number`, `order_number`, `ncr_hours` | open | Return NULL; none is consumed today |
| V-13 | Views for `tblCompany` / `tblSupplier` / `tblEngItemMaster` / `tblProjects` | open | Base-table reads; see the governance note in §2.1 |

## 15. Risks

**A quiet NULL is worse than a loud failure.** An unresolved column does not crash — it makes a component score nothing, which looks like a vendor with no history. `blocking_gaps()` covers the two fields where this would be most damaging; the mitigation for the rest is that no other unresolved column is consumed by any metric.

**Reconciling against an uncharacterised baseline.** §11. The Excel extract's scope is unknown, so "matches Excel" may mean "matches an arbitrary slice". Treat a clean reconciliation as evidence the queries are right, not as evidence the population is.

**`ExcelRepository` must stay frozen during reconciliation.** It is the baseline, and changing it while reconciling against it defeats the exercise. That is why the PO type-validation is now duplicated — `EtoRepository` carries its own copy rather than refactoring `ExcelRepository` into a shared module. This is **new** duplication introduced by this work, not an inherited one (the design document's D-15 is a different case, `fully_received` computed twice). It should be collapsed into a shared `po_contract` module in the Phase 0 batch, after cutover.

**Governance drift.** Four objects are read as base tables because no view is known. If a view exists and is later found, the queries should move to it — that is the whole point of the rule, and `config/eto.json` makes it a one-line change.

**Credentials.** The Excel pipeline had no secrets. This one needs a read-only ETO login. Windows auth is the default and avoids the problem entirely; SQL auth reads `ETO_USER` / `ETO_PWD` from the environment and neither is ever written to disk or logged. `.gitignore` already excludes `.env`.

**Query cost.** The PO query touches a 159k-row view with three joins, unscoped by default. On a SQLEXPRESS instance shared with production ETO users, run it scoped — by project or date — before running it whole, and prefer off-hours for a full extract.
