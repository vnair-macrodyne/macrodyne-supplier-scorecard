# Vendor Scorecard — ETO Database Mapping

**Companion to:** `docs/DESIGN.md` (as-built specification and target-state design)
**Source system:** Total ETO — `Macrodyne_Production` on `MACRO-ETO-SVR\SQLEXPRESS`
**Access:** `TotalETOReportWriter`, read-only
**Schema authority:** `ETO_SCHEMA_MAP.md` (verified 2026-07-04 against declared constraints and live join tests)
**Document date:** 2026-09-03 · **Probe 1 run:** 2026-09-03 against production

---

## What the Probe Settled

The live schema probe answered every column question and overturned three assumptions. In order of consequence:

**1. `vwPurchaseOrderHeader.CName` is the CLEAN company name.** The Excel extract carried the decorated display name — `Bluewater Heater [Oldcastle] (Approved)` — and the scorecard parses the city out of those brackets to form half its grain. Only **1 of 1,701** supplier records has a bracket stored in `tblCompany.CName`; ETO applies the decoration in its display layer. Mapping `vendor_name` to `poh.CName` would have given every vendor a NULL location, collapsed the grain from (name, city) to (name, `None`), and matched zero NCRs — **without raising anything**. §4.1 covers the fix.

**2. The exact-key supplier path reaches 100% of supplier-linked NCRs.** Of 1,941 active NCRs, 578 carry a supplier and 1,363 carry none — and those are *exactly* the same 578 that carry a `PurchaseOrderID`, because ETO derives `SupplierID` from the PO. There is no NCR with a name but no resolvable key. The prototype's 51 unmatched exceptions are an artifact of name parsing, not missing data.

**3. `EstimatedLeadTime` is not empty — and that is a hazard, not a reprieve.** 844 of 87,237 items carry a value, but only **114 are positive**: 730 are a literal `0`. The evaluator accepts any value `>= 0` as a benchmark, so a raw read would judge those lines against a zero-day promise and mark essentially all of them non-adherent — handing affected vendors a Lead-Time D on a 15% weight built entirely on unset data. §10.

Two objects gained a view: **`vwEngItemMaster`** (54 columns, resolves UOM, category and preferred supplier to names) and **`vwProjects`** (41 columns). Both are now used in place of the base tables. `vwSupplier` does not exist.

Every load-bearing column resolved: `PurchaseUOM` on 161,392 of 161,392 lines, `Quantity` on 1,326 of 1,941 NCRs, `QuantityRejected` on 929. `ItemCompanyID` is on the PO detail view directly, so no item-master join is needed. `OrderNumber`, `PurchaseSupplierItem` and `TotalHours` closed the last three unmapped fields.

**Probe 2 then closed the blocking question and narrowed the scope.** `dbo.udfCompanyRetrieveDisplayNames(1)` is callable, and its `Preferred` column is ETO's own display name — so the queries read it rather than rebuilding the string. That mattered: a hand-rebuild matched 571 of 578 NCR suppliers and 957 of 971 receiver-log suppliers, and the misses were suppliers whose status is not "Approved" (`Samco Machinery [Toronto] (Inactive)`). The suffix is a **status**, not a boolean, and a rebuild that assumes otherwise splits those vendors into two scorecard rows.

**What is still open: the scope**, and it is down to two candidates on the PO side — §11.

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

The immediate implication is smaller: prefer a view wherever one exists. The probe took that seriously and found two — `vwEngItemMaster` and `vwProjects` — that this document had been reading as base tables. Both are now used. `vwSupplier` does not exist, so `tblCompany` and `tblSupplier` are the only base-table reads that remain.

### 2.2 Objects used

| Purpose | Object | Type | Basis |
|---|---|---|---|
| PO lines (driver) | `dbo.vwPurchaseOrderDetails` | view | verified |
| PO headers | `dbo.vwPurchaseOrderHeader` | view | verified |
| Receipts per line | `dbo.vwReceiverLogSummed` | view | verified — `PurchaseDetailID`, `SumOfQtyReceived`, `MaxOfDate` |
| Receipt events | `dbo.vwReceiverLog` | view | **50 cols, genuinely event-level** — 9,596 lines have more than one receipt, up to 11 |
| NCRs (driver) | `dbo.vwNonConformances` | view | verified |
| NCR task counts | `dbo.vwNonConformanceList` | view | verified — `Tasks`, `Outstanding` |
| NCR costs | `dbo.vwCostingSummed_ByNC` | view | verified |
| NCR origin dept | `dbo.tlkpNonConformanceOrigin` | lookup | verified |
| Supplier extension | `dbo.tblSupplier` | table | 11 cols — `SupNetTerms`, `SupFOB`, `SupQAApproved`, `DefaultCurrName` |
| Item master | `dbo.vwEngItemMaster` | view | **54 cols — resolves UOM, category, preferred supplier to names** |
| On-hand | `dbo.vwInventory` | view | verified |
| Projects | `dbo.vwProjects` | view | **41 cols — adds customer name, manager, city** |
| Companies | `dbo.tblCompany` | table | 53 cols — `vwSupplier` does not exist |

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

### 4.1 The finding that matters, and the trap underneath it

`PurchaseSupplierID → tblCompany.CompanyID` is a declared foreign key. ETO identifies a supplier by an integer, and the scorecard's whole vendor-identity mechanism is a regex reimplementation of it. That part of the original reading holds.

**What the probe overturned is where the decorated name comes from.** The assumption was that `tblCompany.CName` carries the `NAME [CITY] (Approved)` convention. It does not:

| Source | What it returns |
|---|---|
| `tblCompany.CName` | `Bluewater Heater` — **clean; 1 of 1,701 supplier rows has a bracket** |
| `vwPurchaseOrderHeader.CName` | `Bluewater Heater` — the same clean name |
| `vwNonConformances.Supplier` | `Bluewater Heater [Oldcastle] (Approved)` |
| `vwReceiverLog.Supplier` | `Berendsen-Scarboro [Scarborough] (Approved)` |

The decoration is applied by ETO's display layer, not stored. The Excel PO extract's `Supplier` column was therefore a **display string**, not `poh.CName`.

This is the most dangerous finding in the probe, because of how it fails. Map `vendor_name` to `poh.CName` and `extract_vendor_city()` returns `None` for every row; the grain collapses from (name, city) to (name, `None`); NCR matching requires a non-null city, so it matches **zero**. Nothing raises. The workbook builds, the vendor count changes, and every Quality score reads as perfect.

**Probe 2 resolved it, and the resolution is instructive.** `dbo.udfCompanyRetrieveDisplayNames(1)` is callable by the reporting account, and its `Preferred` column is ETO's own display string — `Macrodyne Technologies Inc. [Concord] (Approved)`. So `@supplier_display_name` reads the function:

```sql
COALESCE(disp.Preferred, co.CName + <city brackets>)   -- fallback only
```

The rebuild is kept solely as a fallback for a company with no row in the function, because a NULL `vendor_name` sends the whole PO line to `rejected_purchase_orders`.

**The rebuild was close but wrong, and it is worth recording how.** Tested against ETO's own strings, a `CName + city + '(Approved)'` reconstruction matched **571 of 578** NCR suppliers and **957 of 971** receiver-log suppliers. The misses all look like this:

| ETO says | The rebuild produced |
|---|---|
| `Samco Machinery [Toronto] (Inactive)` | `Samco Machinery [Toronto]` |

The suffix is a **status**, not a boolean — `SupQAApproved = 0` does not mean "no suffix", it means some other status. A 98.8% match rate would have looked like success in a spot check, and those 7 vendors would have silently split into two scorecard rows each, halving their sample sizes and their evidence. Reading ETO's own function removes the entire class of error.

The function also exposes `CompanyCityNoStatus` — `Name [City]` without the suffix — which ships additively as `supplier_name_no_status`. That is the better grain key long-term: a supplier moving from Approved to Inactive changes `Preferred` and would otherwise fragment its own history.

The lasting answer is not to rebuild a display string at all — it is to key the grain on `CompanyID` and take the city from `co.CCity`. The rebuild exists to hold parity while that change is made deliberately (§13 Step 6).

Two defects resolve either way:

- **D-01** (identity from free text) — the key exists and is declared.
- **D-17** (bracket-position sensitivity) — no parsing, no sensitivity.

This resolves three of the design document's defects at once:

- **D-01** (identity from free text) — the key exists and is declared.
- **D-17** (bracket-position sensitivity) — no parsing, no sensitivity.
- Split vendor rows caused by name spelling variants — one `CompanyID`, one row.

One caveat worth stating plainly: the Excel `Supplier #` column is **not** this key. Caption discovery on 2026-08-14 showed `PurchaseOrderDetailCustom3` is captioned "Supplier #" — a line-level custom *decimal* field, unrelated to `CompanyID`. Do not substitute one for the other.

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

Probe 1 counted all four, and **none of them is 1,803**:

| Scope | Companies |
|---|---|
| all companies | 2,087 |
| `CActive = 1` | 2,061 |
| in `tblSupplier` | 1,701 |
| in `tblSupplier` and `CActive = 1` | 1,688 |
| purchased from (any PO) | 1,089 |
| a project customer | 292 |
| **the Excel extract** | **1,803** |

1,803 sits between "all suppliers" and "all companies", which no single predicate here produces. Probe 2 section C3 tries the unions and the address-completeness filters.

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
| `part_number` | `pod.ItemCompanyID` | verified | on the detail view directly — no item-master join needed |
| `po_part_number` | `pod.PurchaseSupplierItem` | verified | the supplier's own part number |
| `vendor_name` | `@supplier_display_name` | **rebuilt** | `poh.CName` is the CLEAN name — see §4.1 |
| `unit_price` | `pod.PurchasePrice` | verified | |
| `required_date` | `pod.DateRequired` | verified | **detail**, not header — ETO's own late report uses the detail dates and they diverge |
| `revised_date` | `pod.DateRevised` | verified | as above |
| `last_receipt_date` | `rls.MaxOfDate` | verified | configurable; see §6.3 |
| `uom` | `pod.PurchaseUOM` | verified | populated on 161,392 of 161,392 lines |
| `received_qty` | `rls.SumOfQtyReceived` | verified | configurable; NULL filled to 0 |
| `order_date` | `poh.PurchaseDate` | verified | also the created/placed date — ETO keeps no separate entry timestamp |
| `extended_value` | `pod.ExtendedPrice` | verified | pre-computed by the view |
| `currency_code` | `poh.PurchaseCurr` | verified | |
| `currency_rate` | `poh.PurchaseCurrRate` | verified | mapped but unused today; the FX-normalisation hook |
| `supplier_number` | `pod.PurchaseOrderDetailCustom3` | verified | caption = "Supplier #", decimal. **Not** the company key |
| `order_number` | `pod.OrderNumber` | verified | a decimal on the detail line |
| `receiving_date` | `pod.PurchaseOrderDetailCustom5` | verified | caption = "Receiving Date"; populated on <1% of lines |

**Additive** (returned, not consumed): `supplier_company_id`, `supplier_clean_name`, `supplier_city`, `supplier_qa_approved`, `purchase_detail_id`, `item_id`, `item_description`, `supplier_item_desc`, `project_name`, `project_customer`, `remedy_ncr_id`, `detail_received_qty`, `log_received_qty`, `log_last_receipt`.

Every contract column is now resolved. **Nothing on the PO dataset is a guess.**

### 6.5 Part number is on the PO line after all

The first draft of this document routed `part_number` through a join to the item master, on the reasoning that `vwPurchaseOrderDetails` carried only `ItemID` and `ItemDescription`. The probe showed otherwise: the view carries **`ItemCompanyID : nvarchar NOT NULL`** directly, alongside `PurchaseSupplierItem` (the supplier's own part number) and `PurchaseSupplierDescription`.

So `part_number` is `pod.ItemCompanyID`, the join is gone, and the query is one table lighter. The guarantee the join was there to provide survives anyway: `vwEngItemMaster` exposes the same `ItemCompanyID` for the same `ItemID`, so the PO part key and the Item Master part key are the same string by construction.

`item_id`, `item_description` and `supplier_item_desc` ship as additive columns.

### 6.3 Receipt basis — two sources, both returned

ETO exposes fulfilment twice, and the two do not have to agree:

| Source | Columns | Nature |
|---|---|---|
| PO line | `pod.Received` — **and no date at all** | the line's own running quantity |
| Receiver log | `rls.SumOfQtyReceived`, `rls.MaxOfDate` | derived from actual receipt transactions |

The probe removed the choice for dates: **`vwPurchaseOrderDetails` has no `LastReceivedDate` column.** Earlier discovery notes referred to one, but it is not on this view. The receiver log is the only source of a receipt date, so `last_receipt_source: "detail"` degrades to no date and the receiver log is effectively mandatory.

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

**The probe settled it decisively.** Of 1,941 active NCRs:

| Supplier path | NCRs |
|---|---|
| exact key via PO | **578** |
| PO set but supplier unresolved | 0 |
| name only, no PO | **0** |
| no supplier at all | 1,363 |

The 578 with a supplier are *exactly* the 578 with a `PurchaseOrderID` — ETO derives `nc.SupplierID` from the PO, so the two are the same population by construction. **There is no NCR anywhere in the source that has a supplier name but no resolvable key.**

That closes the question behind D-03. The prototype's 51 unmatched supplier-linked NCRs are not a data problem; they are name-parsing failures on records whose supplier ID was available the whole time. Switching the join to `SupplierID` recovers all of them and cannot produce new ones.

The 1,363 with no supplier are internal non-conformances, correctly excluded from supplier scoring.

Both paths ship. `vendor_name` (`nc.Supplier`, already decorated) keeps parity; `supplier_company_id` (`nc.SupplierID`) is the migration target, and `supplier_display_name` is the rebuild, carried alongside so the reconciliation can compare ETO's own string against it.

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
| `quantity` | `nc.Quantity` | verified — populated on 1,326 of 1,941 |
| `quantity_rejected` | `nc.QuantityRejected` | verified — populated on 929 of 1,941 |
| `interim_action` | `nc.RecommendedInterim` | verified |
| `root_cause` | `nc.NonConformanceRootCause` | verified |
| `corrective_pre_action` | `nc.NonConformanceCorrectivePreventiveAction` | verified |
| `ncr_costs` | `ncc.TotalNCCostingValue` | verified |
| `ncr_hours` | `ncc.TotalHours` | verified — on the costing view |
| `target_date` | `nc.NonConformanceCustom5` | verified — caption = "Target Date" |
| `date_follow_up` | — | **`QualityFollowUp` is `nvarchar`, not a date** — see below |
| `created_date` | `nc.CreationDate` | verified |
| `vendor_name` | `nc.Supplier` | verified |
| `item_id` | `nc.ItemID` | verified |

**Additive:** `supplier_company_id`, `po_supplier_company`, `po_supplier_name`, `supplier_display_name`, `quality_follow_up`, `created_date_only`, `ncr_description`, `part_description`, `customer_name`, `nc_id`, `origin_department`, `ncr_labour_hours`, `ncr_purchased_cost`.

Two probe results shape this dataset.

**`QualityFollowUp` is `nvarchar NOT NULL`, not a date.** The Excel column was "Date of follow-up", but ETO's field is free text. Mapping it to `date_follow_up` and coercing would turn every value into `NaT` — data destroyed silently to satisfy a column name. It ships as the additive text column `quality_follow_up` instead, and `date_follow_up` is honestly NULL. Nothing downstream reads either.

**The Excel NCR extract came from `vwNonConformanceList`, not `vwNonConformances`.** `Tasks`, `Outstanding` and `CreationDate_DateOnly` — the Excel headers "Tasks", "Outstanding" and "Created (Date Only)" — exist only on the list view. But `NonConformanceBarcode` (the "NC #") and `SActive` exist only on `vwNonConformances`, so that remains the driver and the list view joins 1:1. `created_date_only` ships additively for exact comparison during reconciliation.

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

### 8.1 The view, not the table

`vwEngItemMaster` exists (54 columns) and is now the driver. It matters because the base table stores UOM and category as **integer IDs** — `ItemUOM : int`, `ItemCategory : int` — while the view resolves them to `UOMDescription` and `CategoryDescription`, and resolves the preferred supplier to a name. The Excel extract carried descriptions, so the view is both the governance-correct and the parity-correct choice.

On-hand quantity is joined `1:1` from a **pre-grouped** subquery. ETO inventory is a shared pool with one row per item × location, so joining `vwInventory` directly would multiply every item row by its location count.

### 8.2 Mapping — `get_items()`

| Contract column | ETO expression | Note |
|---|---|---|
| `part_number` | `im.ItemCompanyID` | populated on 87,237 of 87,237 |
| `description` | `im.ItemDescription` | |
| `uom` | `im.UOMDescription` | resolved by the view |
| `category` | `im.CategoryDescription` | resolved by the view |
| `list_price` | `im.ItemListCost` | |
| `lpp` | `im.ItemLastCost` | LPP reads as Last Purchase Price |
| `revision` | `im.ItemRevNumber` | |
| `quantity_on_hand` | `SUM(vwInventory.QtyOnHand)` per `ItemID` | pre-grouped |
| `preferred_supplier` | `im.PreferredSupplier` | resolved to a name by the view |
| `supplier_part_number` | `im.SupplierPartNumber` | populated on 9,637 of 87,237 |
| `manufacturer` | `im.Manufacturer` | |
| `manuf_part_number` | `im.ManufacturerPartNumber` | populated on 22,990 of 87,237 |
| `lead_time` | `@item_lead_time` | `NULLIF(EstimatedLeadTime, 0)` — see §8.3 |
| `last_supplier` | — | `im.CName` is a supplier name on the view but which one is unproven; left NULL rather than guessed |
| `quantity_reserved` | — | **`ItemReserved` is a `bit` flag, not a quantity.** No equivalent exists |

**Additive:** `item_id`, `raw_lead_time`, `preferred_supplier_id`, `last_supplier_company_id`, `item_reserved_flag`, `obsolete`, `long_lead_flag` (`PartCustom7`), `oversize_flag` (`PartCustom8`).

### 8.3 The zero-lead-time hazard

This is the one place where better data would have made the scorecard **worse**, and it is worth stating plainly because the failure is silent and it costs a grade.

`EstimatedLeadTime` on 87,237 items:

| | Items |
|---|---|
| populated | 844 |
| **of which positive** | **114** |
| of which literal zero | 730 |

`lead_time_evaluator` accepts any benchmark `>= 0`. Read raw, those 730 zeros become real zero-day promises: `actual_lead_time_days <= 0` is false for anything that took a day to arrive, so essentially every line against those parts is marked non-adherent. A vendor with five such lines clears the minimum sample and takes a Lead-Time score near 0 — a **D on a 15% weight, computed entirely from parts where nobody entered a lead time.**

A zero here means "unset", not "same day". `@item_lead_time` says so with `NULLIF(EstimatedLeadTime, 0)`, which is why `lead_time` is the only item column that is not a bare reference.

`tblEngItemMaster` is also where the maintained `PartCustom7` / `PartCustom8` flags live (long-lead and oversize) — real, maintained data, and a candidate if item risk ever enters the scorecard.

## 9. What the Database Changes

Moving to ETO is not just a plumbing change. Five of the design document's defects become fixable, and two become measurable for the first time.

| Design doc defect | What ETO provides | Status after migration |
|---|---|---|
| **D-01** vendor identity from free text | `PurchaseSupplierID` → `CompanyID`, a declared FK | **Fixable now.** Both keys ship; switching the grain is a config-and-groupby change. |
| **D-03** Quality numerator/denominator scope mismatch | NCR → PO → supplier exact key | **Fully fixable — proven.** All 578 supplier-linked NCRs carry a resolvable `SupplierID`; zero have a name without a key. |
| **D-17** bracket-position parsing bug | no parsing at all | **Gone** with D-01. |
| **D-05** no evaluation period | `poh.PurchaseDate` and `nc.CreationDate` are queryable date columns | **Fixable now.** `scope.po_date_from` / `po_date_to` exist; a rolling window is a config value, not a code change. |
| **D-06** unvalidated `resolved` contract | `bit` with a known type | **Fixed** — the repository normalises the value and raises on anything it cannot map, instead of letting a text value silently zero the component (§7.3). |
| **D-09** no run history | the `scorecard` schema in `docs/DESIGN.md` §15.3 | Unblocked — persistence has somewhere to live. |
| Responsiveness proxy | `Released`, `Custom5` target date, task counts | **Improvable now**, no new source needed. |
| Partial receipts excluded from OTD | `vwReceiverLog` event-level receipts | **Confirmed available.** 50 columns, one row per receipt; 9,596 lines have more than one, up to 11. The biggest available OTD improvement. |
| **D-15** duplicated business rules | — | **Unchanged.** ETO does not help here, and this migration adds a second copy of the PO type validation (§15). |
| Project blindness | `ProjectID` on PO and NCR | **Delivered** as attribute + filter. |
| Cross-currency | `PurchaseCurrRate` on the header | Available; needs an agreed FX rule (open decision #11). |

---

## 10. What the Database Does Not Fix

**Lead-Time still cannot be scored — but for a different reason than this document first recorded.**

The earlier finding, from the Project Console's 2026-07-25 late-PO work, was that `EstimatedLeadTime` is empty on every item. That is no longer true. The probe found 844 of 87,237 items populated — and only **114 with a positive value**.

So the correction runs both ways:

- It is **not** an empty column. D-04 was overstated as "the benchmark does not exist in this source".
- It is **0.13% coverage**, which is not a benchmark either. The minimum sample is five eligible lines per vendor; at that density almost no vendor will reach it, and the component stays `INSUFFICIENT BENCHMARK DATA` for practically all of them.
- And the 730 zeros are actively dangerous if read raw (§8.3).

Probe 2 turned "practically none" into a number: **159 PO lines out of 161,392 join to a positive lead time — 0.099%.** Spread across suppliers, against a minimum sample of five eligible lines per vendor, that is at most a handful of vendors and realistically none.

The two options are unchanged, and both still need a business decision (open decision #7):

1. **Drop the component** from the published weighting until coverage exists. A permanently-N/A component makes Weight Coverage % harder to read and implies evidence that is never coming.
2. **Change the benchmark** to something ETO holds densely — the PO's own need-by date against the order date is a *promise-to-delivery* measure rather than a *quoted-lead-time* measure, and it is fully populated.

Option 2 is a different metric, not the same metric with better data. Worth saying out loud before anyone adopts it.

The third possibility — that the 114 populated items might be concentrated in parts actually bought, making the component scoreable for a small named set of vendors — **is closed.** 159 lines is not a population; it is noise.

**Recommendation: drop Lead-Time from the published weighting.** Redistribute its 15% across the components that have evidence, and record the benchmark as a data-capture gap for Purchasing rather than a scorecard component permanently reading N/A. Carrying a component that can never score makes Weight Coverage % harder to read and implies evidence that is not coming. This is now well enough evidenced to put to the business as a recommendation rather than an option (open decision #7).

## 11. The Scope Question

**This is now the only thing standing between here and a reconciliation, and the probe made it harder rather than easier.**

| Dataset | Excel extract | ETO population | Explained? |
|---|---|---|---|
| Purchase orders | 23,344 (post-cleaning) | 161,392 lines | **no** |
| NCRs | 1,248 | 1,941 (`SActive = 1`, which is all of them) | **no** |
| Vendors | 1,803 | 2,087 companies / 1,701 suppliers | **no** |
| Item master | 86,730 | 87,237 | **yes** — the same query, run earlier |

### 11.1 The PO scope resists every filter

| Scope | Lines |
|---|---|
| all lines | 161,392 |
| header active | 161,392 |
| active + not archived | 161,392 |
| active + not archived + issued | 159,464 |
| issued + has receipt | 157,223 |
| **the Excel extract** | **23,344 after cleaning** |

`PurchaseActive` and `Archived` exclude nothing at all. The issued filter removes 1,928 lines — the draft backlog. Nothing here is within an order of magnitude of 23,344, which is roughly one seventh of the population.

Nor is it a plain year: issued lines run 10,866 (2026) to 26,068 (2022), and 2023 alone is 23,031 — close, but a year window would have to be *raw* above 23,344, and 2023 is below it.

**Probe 2 narrowed it to two candidates**, both close enough that a row count alone cannot separate them:

| Candidate | Raw lines | Cleaning would remove |
|---|---|---|
| **`BuyerID = 43`** | 23,397 | 53 (0.23%) |
| **projects 9000 + 192085** | 23,784 | 440 (1.85%) |
| 12-month window | 19,656 | *impossible — below the extract* |
| 18-month window | 28,759 | 5,415 (18.8%) |

`BuyerID 43` is the closer fit, and a buyer-filtered "PO Status" export is a plausible origin for a file named `PO STATUS_TEST.xlsx`. But the top-two-projects reading is not absurd either: project 9000 is the overhead project and 192085 is the largest real job.

A row count is weak evidence — two different populations can share one. The extract left a **whole signature** though: 20,181 delivery-eligible, 13,164 on-time, 7,017 late, 22,488 commercial-base-eligible, 416 vendor rows. `tools/eto_scope_fingerprint.py` computes all six for every candidate using ETO's own delivery rule. A wrong scope will not reproduce all six at once.

### 11.2 Why it matters more than it looks

Two consequences, and the second is the uncomfortable one.

**Reconciliation is not interpretable without it.** A SQL run against the wrong population produces a large diff that says nothing about whether the queries are correct.

**The prototype's published figures describe an unidentified slice.** 416 vendor rows, 106 scored, 20,181 delivery-eligible, the A/B/C/D distribution — every one of them describes 14% of the purchasing history, chosen by a process nobody recorded. If that slice turns out to be one buyer's saved filter or a stale date range, the grades do not mean what the workbook says they mean. That is worth knowing before any of them reach a supplier conversation.

**If the fingerprint does not single out a candidate**, the honest conclusion is that the extract is not reproducible, and the ETO run becomes the new baseline with an explicitly chosen scope — a rolling window, or a named project set. That is a better outcome than a scope that merely happens to match a row count.

### 11.3 The other two datasets are harder, not easier

**NCRs.** 1,248 extracted against 1,941 today. A 2023-onward window gives 1,246 *now* — two off — but the extract is older, and a fixed window applied earlier would have returned **fewer**, not more. A date filter cannot explain it. The untested hypothesis is that the NCR extract shares the PO extract's scope; probe 3 §B tests that directly, and also checks whether 1,248 is simply the total at an earlier date.

**Vendors.** 1,803 extracted, of which 58 were incomplete — 3.2%. All 1,701 suppliers today contain **211 incomplete, 12.4%**. The extract is both *bigger* and *four times cleaner* than the supplier table, which no simple filter produces. Something about the extract's definition of a vendor differs from `tblSupplier` membership. Probe 3 §C hunts it, including the display-name function's own `IsSupplier` flag.

## 12. Code Architecture

### 12.1 New files

```
config/eto.json                        connection, scope, options, and every column expression
src/data_access/eto_queries.py         SQL construction + contracts + validation
src/data_access/sql_repository.py      EtoRepository(VendorScorecardRepository)
src/data_access/repository_factory.py  source selection + a closing context manager
tools/eto_schema_probe.py              probe 1 — read-only column and object discovery (RUN)
tools/eto_scope_probe.py               probe 2 — display name (RUN), and the extract scopes
tools/eto_scope_fingerprint.py         probe 3 — identify the scope by the extract's signature
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

**Step 1 — Probe 1. DONE (2026-09-03).** Every column question answered; three assumptions overturned. See *What the Probe Settled*.

**Step 2 — Resolve columns. DONE.** `config/eto.json` carries no guesses on the PO, NCR or vendor datasets. Two item columns are honestly NULL because ETO has no equivalent (`last_supplier`, `quantity_reserved`), and neither is consumed.

**Step 3 — Probe 2. DONE (2026-09-03).** The blocking question is closed: the queries read ETO's own display-name function rather than rebuilding the string.

**Step 4 — Probe 3, then settle the scope.** Run `tools/eto_scope_fingerprint.py` and paste the output. It computes the extract's full six-measure signature for each candidate scope, so the answer rests on evidence rather than a matching row count. Set `scope` and flip `scope_confirmed` to `true` only when a candidate reproduces the whole signature (§11).

**Step 5 — Reconcile.** `python tools/reconcile_sources.py`. Target: stage totals matching, or every difference explained. Expect the vendor diff to show rows only in one source — those are vendor-identity differences, and they are the D-01 evidence.

**Step 6 — Cut over.** Run `python main.py --source=eto`. The workbook should reproduce. Nothing needs editing: the Excel path stays the default, and it remains both the rollback and the regression baseline.

Run `python tools/dao_conformance_check.py` first — it takes seconds, needs no database, and fails loudly if anything in the DAO contract has drifted.

**Step 7 — Then improve.** In dependency order, each as its own reviewable change:

1. Evaluation period (`scope.po_date_from` / `po_date_to`) — D-05, and it makes every later comparison meaningful.
2. Vendor key → `supplier_company_id` — D-01, D-03, D-17.
3. Responsiveness → days-to-close and on-time-closure — §7.4.
4. Receipt-event OTD with partial receipts — probe F.
5. Quality evidence gate — D-02, unchanged by the migration and still the largest scoring distortion.
6. Persist runs to the `scorecard` schema — D-09.

**Steps 1–5 change no numbers by design. Step 6 changes numbers on purpose.** Keeping that line sharp is what lets anyone tell the two apart later.

---

## 14. Open Verification Items

Probe 1 closed most of this list. What remains, with the consequence of each.

| # | Item | Status | If unresolved |
|---|---|---|---|
| V-01 | **`@supplier_display_name` reproduces ETO's string exactly** | **BLOCKING** | The grain collapses to (name, `None`) and NCR matching returns zero — silently. Probe 2 §A compares it character-for-character against `nc.Supplier` and `vwReceiverLog.Supplier`, and tests whether `udfCompanyRetrieveDisplayNames(1)` is callable so the rebuild can be dropped |
| V-02 | **PO scope that yields ~23,344+** | open | Reconciliation is not interpretable (§11). Probe 2 §B |
| V-03 | **NCR scope that yields 1,248** | open | 1,941 available; no sub-population found yet. Probe 2 §C1–C2 |
| V-04 | **Vendor scope that yields 1,803** | open | Sits between 1,701 suppliers and 2,087 companies. Probe 2 §C3 |
| V-05 | Lead-time coverage on lines actually purchased | open | Decides whether §10 option 1 or 3 applies. Probe 2 §D2 |
| V-06 | `im.CName` on `vwEngItemMaster` — is it the last supplier? | open | `last_supplier` stays NULL; not consumed |
| V-07 | Views for `tblCompany` / `tblSupplier` | open | Base-table reads; `vwSupplier` confirmed not to exist. See §2.1 |

**Closed by probe 1:** every PO, NCR and vendor contract column; `PurchaseUOM` presence *and* population; the NCR quantities; `ItemCompanyID` on the PO line; `OrderNumber`; `PurchaseSupplierItem`; `TotalHours`; `PurchasePrinted` / `PurchaseEmailed` on the view; the `tblCompany` address block; the `tblSupplier` attributes; `vwReceiverLog`'s event-level shape; the existence of `vwEngItemMaster` and `vwProjects`; and the `EstimatedLeadTime` correction.

## 15. Risks

**A quiet wrong answer is worse than a loud failure**, and this migration has now produced two examples of the shape. The supplier display name (§4.1) would collapse the grain without raising; the zero lead times (§8.3) would hand out D grades computed from unset data. Both were caught by asking what the data actually contains rather than what the column is called, and neither would have been caught by a test that only checked the pipeline ran. `blocking_gaps()` covers the columns whose absence is silent; it cannot cover a column that is present and wrong, which is why probe 2 §A exists.

**Reconciling against an uncharacterised baseline.** §11. The Excel extract's scope is unknown, so "matches Excel" may mean "matches an arbitrary slice". Treat a clean reconciliation as evidence the queries are right, not as evidence the population is.

**`ExcelRepository` must stay frozen during reconciliation.** It is the baseline, and changing it while reconciling against it defeats the exercise. That is why the PO type-validation is now duplicated — `EtoRepository` carries its own copy rather than refactoring `ExcelRepository` into a shared module. This is **new** duplication introduced by this work, not an inherited one (the design document's D-15 is a different case, `fully_received` computed twice). It should be collapsed into a shared `po_contract` module in the Phase 0 batch, after cutover.

**Governance drift.** Four objects are read as base tables because no view is known. If a view exists and is later found, the queries should move to it — that is the whole point of the rule, and `config/eto.json` makes it a one-line change.

**Credentials.** The Excel pipeline had no secrets. This one needs a read-only ETO login. Windows auth is the default and avoids the problem entirely; SQL auth reads `ETO_USER` / `ETO_PWD` from the environment and neither is ever written to disk or logged. `.gitignore` already excludes `.env`.

**Query cost.** The PO query touches a 159k-row view with three joins, unscoped by default. On a SQLEXPRESS instance shared with production ETO users, run it scoped — by project or date — before running it whole, and prefer off-hours for a full extract.
