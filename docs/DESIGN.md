# Macrodyne Vendor Scorecard — Design Document

**Repository:** `macrodyne-supplier-scorecard`
**Status:** Prototype (as-built) + proposed target state
**Document date:** 2026-09-03
**Audience:** IT / Supply Chain stakeholders (Sections 1–3, 6, 10, 11) and developers (all sections)

---

## Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [System Overview](#2-system-overview)
3. [As-Built Architecture](#3-as-built-architecture)
4. [Configuration Design](#4-configuration-design)
5. [Source Data Contracts](#5-source-data-contracts)
6. [Canonical Data Model and Grain](#6-canonical-data-model-and-grain)
7. [Pipeline Design — Stage by Stage](#7-pipeline-design--stage-by-stage)
8. [Metric Component Specifications](#8-metric-component-specifications)
9. [Scoring and Grading Engine](#9-scoring-and-grading-engine)
10. [Exception Handling Design](#10-exception-handling-design)
11. [Output Design — Excel Workbook](#11-output-design--excel-workbook)
12. [Cross-Cutting Concerns](#12-cross-cutting-concerns)
13. [Known Defects, Risks and Design Debt](#13-known-defects-risks-and-design-debt)
14. [Open Business Decisions](#14-open-business-decisions)
15. [Target-State Design](#15-target-state-design)
16. [Migration and Refactor Plan](#16-migration-and-refactor-plan)
17. [Appendix A — Derived Field Dictionary](#appendix-a--derived-field-dictionary)
18. [Appendix B — Status Vocabulary](#appendix-b--status-vocabulary)
19. [Appendix C — Mapped-but-Unused Source Fields](#appendix-c--mapped-but-unused-source-fields)

---

## 1. Purpose and Scope

### 1.1 Business purpose

The Vendor Scorecard consolidates purchasing, non-conformance (NCR), Item Master and Vendor Master data into a single vendor-level performance view, expressed as five weighted components and an overall A/B/C/D grade. It exists to give Supply Chain an objective, repeatable basis for supplier review, and to expose where the underlying source data is not yet good enough to support that judgement.

### 1.2 Design intent

The prototype was deliberately built around one principle: **never manufacture confidence the data does not support.** Every design decision below follows from it.

| Principle | Mechanism in code |
|---|---|
| Missing data must not look like bad performance | Unavailable components are `N/A`; overall score renormalizes over available weight rather than treating gaps as zero |
| Missing data must not look like good performance | Minimum-sample gates per component; minimum 3 scored components for an overall score *(this principle is currently violated for Quality — see D-02)* |
| Uncertain matches are surfaced, not forced | Supplier-linked NCRs that cannot be matched exactly are exported as exceptions rather than fuzzy-matched into a vendor |
| Linkage ≠ responsibility | "supplier-linked NCR" is explicitly not "supplier-responsible NCR" anywhere in the code or output |
| Policy is not code | Weights, thresholds and sample minimums live in `config/scorecard_rules.json`, outside the metric logic |
| Currencies are never silently pooled | Commercial comparisons are scoped to a single currency and UOM; no cross-currency summation |

### 1.3 Scope of this document

Covers the pipeline as committed today (single commit `b0354b8`, plus uncommitted working-tree changes to every source file), the design rationale behind it, the defects and gaps found by inspection, and the proposed production architecture on ETO.

**Out of scope:** approval of any weight, threshold or metric definition. Every number in Sections 4, 8 and 9 is a prototype assumption pending business sign-off (Section 14).

---

## 2. System Overview

```
data/input/*.xlsx  ──►  ExcelRepository  ──►  validated DataFrames
                                                     │
                            ┌────────────────────────┼────────────────────────┐
                            ▼                        ▼                        ▼
                     Vendor Master            Purchase Orders               NCRs
                     quality checks          (vendor prep, delivery,     (supplier link,
                            │                 lead time, commercial)      quality, resolution)
                            │                        │                        │
                            │                        ▼                        ▼
                            │            aggregate to Vendor+Location  ──► NCR match
                            │                        │        ◄─────────────┘   │
                            │                        ▼                          ▼
                            │                 vendor_summary  ◄── matched NCR aggregate
                            │                        │                    unmatched NCRs
                            │                        ▼                          │
                            │             scoring / grading engine              │
                            │                        │                          │
                            └────────────────────────┼──────────────────────────┘
                                                     ▼
                                     Vendor_Scorecard_Prototype.xlsx
                                     (5 worksheets)
```

**Execution model:** single-process batch. `python main.py` from the repository root. No arguments, no scheduling, no service. Runtime is dominated by four `pd.read_excel` calls (≈113k rows total) and by six `DataFrame.apply(..., axis=1)` passes over 416 rows in the scoring engine.

**Dependencies:** `pandas`, `openpyxl` (unpinned). Standard library: `json`, `re`, `pathlib`, `abc`, `difflib`, `numbers`, `datetime`.

---

## 3. As-Built Architecture

### 3.1 Layering

The code is organised into seven layers, each a package under `src/`. The layering is genuinely enforced — no evaluation module imports a reporting module, and no aggregation module reads a file.

| Layer | Package | Responsibility | Depends on |
|---|---|---|---|
| Data access | `src/data_access` | Load, rename, validate source datasets | config JSON, pandas |
| Data quality | `src/quality` | Vendor Master completeness and duplicate detection | pandas |
| Matching | `src/matching` | Derive canonical vendor identity from free text | `re`, pandas |
| Evaluation | `src/evaluation` | Transaction-level metric preparation (4 modules) | matching (NCR only), pandas |
| Aggregation | `src/aggregation` | Roll transactions to Vendor + Location | pandas |
| Scoring | `src/scoring` | Apply configured rules → scores, grades, coverage | config JSON, pandas |
| Reporting | `src/reporting` | Build and format the Excel workbook | config JSON, openpyxl, pandas |
| Orchestration | `main.py` | Sequence the stages, print diagnostics | all of the above |

### 3.2 Module map

```
main.py                                  1,825  orchestration + diagnostics (script, not a module)
config/
  column_mappings.json                      82  source → internal column names, 4 datasets
  scorecard_rules.json                      47  weights, thresholds, sample minimums, penalty factor
  sources.json                              14  dataset key → filename
src/
  data_access/
    base_repository.py                      20  VendorScorecardRepository (ABC, 4 methods)
    excel_repository.py                     99  Excel implementation + PO validation
  quality/
    vendor_quality.py                      275  completeness, exact duplicates, review status (+ 3 unused fns)
  matching/
    vendor_matcher.py                       46  normalize_vendor_name, extract_vendor_city
  evaluation/
    delivery_evaluator.py                   47  target date, eligibility, on-time/late, days late
    lead_time_evaluator.py                 355  part-key normalisation, Item Master benchmark, adherence
    commercial_evaluator.py                258  price grouping, previous price, stability, change %
    ncr_evaluator.py                       161  supplier link, quantity validation, resolution proxy
  aggregation/
    vendor_aggregator.py                   119  PO → Vendor+Location, 18 aggregates + 3 percentages
    ncr_aggregator.py                      182  matched NCR → Vendor+Location, 9 aggregates + 2 percentages
  scoring/
    vendor_scoring.py                      998  5 component scorers, grading, overall + coverage
  reporting/
    excel_exporter.py                    2,758  5 worksheets, styling, INDEX/MATCH detail view
```

### 3.3 The key architectural asset

`VendorScorecardRepository` is an abstract base class with exactly four methods:

```python
get_items()  get_purchase_orders()  get_ncrs()  get_vendors()
```

`ExcelRepository` is one implementation. **Nothing downstream of the repository knows the data came from Excel.** This is the single most valuable design decision in the codebase: the ETO database migration described in Section 15 is, at the boundary, one new class implementing four methods. Everything from Stage 1 onward can remain unchanged.

The abstraction is currently leaky in one place — `ExcelRepository.get_purchase_orders()` performs both header-row stripping (an Excel artifact) and business validation (not an Excel artifact). Section 16 proposes separating them.

---

## 4. Configuration Design

Three JSON files. All are read at runtime; none are validated against a schema.

### 4.1 `config/sources.json`

Maps a logical dataset key to a filename inside `data/input/`.

```json
{ "items": {"filename": "ItemMaster.xlsx"},
  "purchase_orders": {"filename": "PO STATUS_TEST.xlsx"},
  "ncrs": {"filename": "NCR_STATUS.xlsx"},
  "vendors": {"filename": "Complete Vendors List 1.xlsx"} }
```

The four keys are hard-referenced by `ExcelRepository`; adding a dataset requires code. The `{"filename": ...}` wrapper object exists so connection details (server, view, schema) can be added per dataset without changing the file's shape — a deliberate hook for the SQL implementation.

### 4.2 `config/column_mappings.json`

Source header → internal snake_case name, per dataset. Serves two purposes:

1. **Contract enforcement.** The mapping's key set *is* the required-column set. `_load_dataset` computes `expected − actual` and raises `ValueError` listing every missing column. A source extract that drops or renames a column fails loudly at load, not silently at metric time.
2. **Decoupling.** Downstream code references `vendor_name`, never `Supplier`. An ETO view exposing different headers is absorbed by editing JSON.

The mapping is also, in effect, the **column-level dependency declaration** — which makes Appendix C (mapped but never read) a useful audit.

### 4.3 `config/scorecard_rules.json`

The entire policy surface. Loaded twice — once by `main.py` for scoring, once independently by `excel_exporter._load_scorecard_rules()` for the Prototype Notes sheet — so the workbook always documents the rules that produced it.

```json
{
  "prototype": true,
  "grade_thresholds": { "A": 90, "B": 80, "C": 70, "D": 0 },
  "overall": { "minimum_available_components": 3 },
  "components": {
    "on_time_delivery": { "label": "On-Time Delivery",      "weight": 25, "minimum_sample": 5 },
    "quality":          { "label": "Quality / NCR",          "weight": 25,
                          "minimum_po_transactions": 5, "ncr_rate_penalty_factor": 5 },
    "lead_time":        { "label": "Lead-Time Performance",  "weight": 15, "minimum_sample": 5 },
    "responsiveness":   { "label": "Responsiveness Proxy",   "weight": 15, "minimum_sample": 3 },
    "commercial":       { "label": "Commercial Performance", "weight": 20, "minimum_sample": 5 }
  }
}
```

Design notes:

- **Weights need not sum to 100.** `total_configured_weight` is computed as the actual sum, and coverage % is expressed against it, so the weighting can be rebalanced without touching the scoring code.
- **But the workbook does not follow the config.** `excel_exporter.py` hardcodes the five weights as literal strings in the Vendor Detail component table and again in the Prototype Notes definitions, and states the penalty factor as literal prose (`"100 - (Supplier-Linked NCR Rate % × 5)"`). Changing a weight in JSON changes the arithmetic but leaves Sheets 2 and 3 documenting the old policy (D-22).
- **Grade thresholds are order-independent.** `_assign_grade` sorts by threshold descending at call time, so grades can be added (`"F": 0`) or reordered in JSON without touching code.
- **`quality` deliberately uses different key names** (`minimum_po_transactions`, not `minimum_sample`) because its gate is on purchasing activity rather than on quality events. Each scorer reads its own component's keys, so no shared schema is imposed.
- **No validation.** A missing key raises `KeyError` deep inside a `DataFrame.apply`, producing a poor error message. A weight of `"25"` (string) would corrupt arithmetic silently.

---

## 5. Source Data Contracts

### 5.1 Purchase Orders — `PO STATUS_TEST.xlsx`

The only dataset with real validation. 23,344 valid rows after cleaning.

**Load sequence in `get_purchase_orders()`:**

1. `_load_dataset` — column-presence check, rename.
2. **Header-row removal.** Rows where `part_number`, `vendor_name`, `ordered_qty` *and* `order_date` are all null are treated as report headers/spacers from the ETO export and dropped.
3. **Required-field split.** Rows missing any of `po_number`, `vendor_name`, `part_number`, `ordered_qty`, `order_date` are moved to `self.rejected_purchase_orders`; the rest become the working set. Counts are printed.
4. **Type validation.** `ordered_qty`, `received_qty`, `unit_price`, `extended_value` must be numeric dtype; `order_date`, `required_date`, `revised_date`, `last_receipt_date` must be datetime dtype. Any failure raises `ValueError`, which `main.py` catches and converts into a pipeline stop.

Type validation runs *after* the split, so a single bad cell in a row that was going to be rejected anyway cannot fail the run. This ordering is correct and intentional.

| Source column | Internal | Used for |
|---|---|---|
| `PO #` | `po_number` | transaction/distinct counts |
| `Supplier` | `vendor_name` | **vendor identity (free text)** |
| `Internal Part No.` | `part_number` | lead-time and commercial keys |
| `Qty` / `Qty Received` | `ordered_qty` / `received_qty` | receipt completeness |
| `PO Date` | `order_date` | lead time, price sequencing |
| `Date Required` / `Date Revised` | `required_date` / `revised_date` | delivery target date |
| `Last Recd Date` | `last_receipt_date` | delivery and lead-time actuals |
| `Price` | `unit_price` | commercial |
| `Currency` / `UOM` | `currency_code` / `uom` | commercial grouping |
| `Ext. Price` | `extended_value` | validated only, never read |

### 5.2 Item Master — `ItemMaster.xlsx`

86,730 rows. No validation beyond column presence. Only `part_number` and `lead_time` are consumed.

### 5.3 NCRs — `NCR_STATUS.xlsx`

1,248 rows. **No type validation.** Consumed fields: `vendor_name`, `ncr_number`, `quantity`, `quantity_rejected`, `resolved`.

`resolved` is compared with `.eq(True)` and `.eq(False)`. If the ETO extract ever emits `"Yes"`/`"No"`, `"Y"`/`"N"` or `1`/`0`-as-text, both flags become `False` while `responsiveness_eligible` stays `True` — every vendor's responsiveness silently drops to 0%. This is an unguarded contract (D-06).

The NCR extract also carries `total_tasks`, `outstanding_tasks`, `target_date`, `date_follow_up`, `created_date`, `root_cause`, `corrective_pre_action`, `ncr_costs` and `ncr_hours` — **all mapped, none consumed.** Section 15.4 proposes using them.

### 5.4 Vendor Master — `Complete Vendors List 1.xlsx`

1,803 rows. No validation. Consumed for the Vendor Review exception sheet only.

**Critical:** the Vendor Master is *never joined to the scorecard.* `company_id` (ETO `CompanyID`) is loaded, renamed, and used only as a display column in the review sheet. Vendor identity throughout the scorecard is derived from parsing the PO `Supplier` string. See D-01.

---

## 6. Canonical Data Model and Grain

### 6.1 Scorecard grain

**One row per (`vendor_match_name`, `vendor_match_city`).** 416 rows in the current run. Both parts are derived, not sourced.

### 6.2 Vendor identity derivation

`src/matching/vendor_matcher.py` parses the PO `Supplier` free-text string, which follows the convention `NAME [CITY] (APPROVED)`.

```python
normalize_vendor_name(s):
    strip → upper → re.sub(r"\s*\[[^\]]+\]\s*(?:\(APPROVED\))?\s*$", "", s) → strip
extract_vendor_city(s):
    re.search(r"\[([^\]]+)\]") → group(1) → strip → upper
```

| Input | `vendor_match_name` | `vendor_match_city` |
|---|---|---|
| `Acme Steel [BRAMPTON] (APPROVED)` | `ACME STEEL` | `BRAMPTON` |
| `Acme Steel [BRAMPTON]` | `ACME STEEL` | `BRAMPTON` |
| `Acme Steel` | `ACME STEEL` | `None` |
| `Acme [ON] Steel [BRAMPTON]` | `ACME [ON] STEEL` | `ON` |

The same two functions are reused verbatim by `ncr_evaluator`, which is what makes PO↔NCR matching possible at all. Row 4 shows the failure mode: only a *trailing* bracket group is stripped, but the *first* bracket group is extracted as the city, so a mid-string bracket desynchronises name from location (D-17).

Grouping uses `dropna=False`, so vendors with no parseable location form a legitimate `(NAME, None)` row rather than disappearing.

### 6.3 Entity relationships (as-built)

```
Vendor Master ─── company_id ───► (NOT JOINED — exception reporting only)

Purchase Order line ── vendor_name ──parse──► (name, city) ◄──parse── NCR ── vendor_name
        │                                          │
        └── part_number ──normalise──► Item Master.part_number → lead_time
```

The vendor identity spine is a parsed string on both sides. `supplier_number` (PO `Supplier #`) is mapped and available on every PO row but never read — a stable key sitting unused next to the free-text one being parsed (D-01).

---

## 7. Pipeline Design — Stage by Stage

`main.py` runs 14 stages inside a single `else:` block guarded by `has_errors`. Each stage prints a diagnostic block; several print reconciliation controls (row counts before/after) that are effectively assertions-by-eyeball.

| # | Stage | Function | Effect on data |
|---|---|---|---|
| 0 | Load + validate | `repo.get_*()` | 4 DataFrames or `has_errors = True` → stop |
| 1 | Vendor Master quality | `classify_vendor_completeness`, `identify_exact_duplicates`, `assign_vendor_review_status` | +7 columns on `vendors` |
| 2 | PO vendor prep | `prepare_purchase_order_vendors` | +`vendor_match_name`, `vendor_match_city` |
| 3 | Delivery | `prepare_delivery_metrics` | +7 columns |
| 4 | Lead-time diagnostic | inline | prints Item Master lead-time coverage |
| 5 | Lead-time | `prepare_lead_time_metrics` | +7 columns; **raises if row count changes** |
| 6 | Commercial | `prepare_commercial_metrics` | +9 columns; row order preserved and restored |
| 7 | NCR prep | `prepare_ncr_metrics` | +10 columns on `ncr_data` |
| 8 | PO aggregation | `aggregate_purchase_orders_by_vendor` | 23,344 rows → 416 |
| 9 | NCR → vendor match | inline merge with `indicator=True` | matched / unmatched split |
| 10 | Fallback diagnostic | inline | `po_location_count` per vendor name; **computed, never used to match** |
| 11 | Matched NCR aggregation | `aggregate_ncrs_by_vendor` | matched NCRs → vendor grain |
| 12 | NCR merge | left join + null fill | NCR columns onto `vendor_summary`; absent → 0 |
| 13 | Scoring | `apply_vendor_scoring` | +21 columns (NCR rate, 5×score, 5×status, 5×grade, 5 overall) |
| 14 | Export | `export_vendor_scorecard` | workbook written |

**Stage 5 row-count assertion** is the strongest correctness guard in the pipeline:

```python
if purchase_df.shape[0] != original_po_row_count:
    raise ValueError("Lead-time merge changed the PO row count.")
```

combined with `validate="many_to_one"` on the merge. A duplicated Item Master part cannot silently fan out PO rows and inflate every downstream count. The same protection is not present on the Stage 12 NCR merge, which relies instead on `ncr_summary` being a groupby result (unique by construction).

**Stage 12 null-fill** sets `total_rejected_qty`, `total_ncr_quantity` and `quality_rejected_qty` to `0` and the six NCR count columns to `0` for vendors with no matched NCR. This is the mechanism behind D-02: "no NCRs found" becomes numerically identical to "zero defects".

---

## 8. Metric Component Specifications

Every component follows the same four-step shape: **transaction-level eligibility predicate → transaction-level classification → vendor-level aggregation → percentage with a guarded denominator.** Percentages are always `.where(denominator > 0)` so a zero denominator yields `NaN`, never `inf` or `0`.

### 8.1 On-Time Delivery — weight 25, minimum sample 5

**Target date**

```
target_date = revised_date if present else required_date
```

**Eligibility**

```
fully_received     = ordered_qty > 0 AND received_qty >= ordered_qty
delivery_eligible  = fully_received AND target_date NOT NULL AND last_receipt_date NOT NULL
```

**Classification**

```
on_time = delivery_eligible AND last_receipt_date <= target_date
late    = delivery_eligible AND last_receipt_date >  target_date
days_late      = max(last_receipt_date − target_date, 0), only where eligible
late_days_only = days_late, only where late
```

**Vendor metric**

```
OTD % = on_time_count / delivery_eligible_count × 100
Average Days Late = mean(late_days_only)   ← mean over LATE lines only, not all lines
```

**Score** = OTD %, clamped 0–100.

**Current results:** 20,181 eligible, 13,164 on-time, 7,017 late.

**Design limitations**
- Uses the aggregate `Last Recd Date`, not receipt-event history. A line received in three shipments is judged only by the last one.
- `fully_received` gate excludes every partially-received line entirely — neither on-time nor late, simply invisible.
- Over-receipts (`received > ordered`) count as fully received.
- There is no tolerance window; one day late is late.
- Zero-quantity lines are excluded by `ordered_qty > 0`.

### 8.2 Quality / NCR — weight 25, minimum 5 PO transactions

Two distinct metrics live here; only the first drives the score.

**Scoring metric — Supplier-Linked NCR Rate**

```
NCR Rate %     = supplier_linked_ncr_count / po_transaction_count × 100
Quality Score  = clamp(100 − NCR Rate % × penalty_factor, 0, 100)      penalty_factor = 5
```

| NCR Rate | Score | Grade |
|---|---|---|
| 0% | 100 | A |
| 2% | 90 | A |
| 4% | 80 | B |
| 6% | 70 | C |
| 10% | 50 | D |
| ≥20% | 0 | D |

**Descriptive metric — NCR Rejected %** (severity, not score)

```
quantity_anomaly = quantity_rejected > quantity            ← flagged, then excluded
quality_eligible = supplier_linked AND quantity NOT NULL AND quantity_rejected NOT NULL
                   AND quantity > 0 AND quantity_rejected >= 0 AND NOT quantity_anomaly

NCR Rejected % = Σ quality_rejected_quantity / Σ quality_quantity × 100
```

The anomaly guard exists because the source contains records like quantity 12 / rejected 60. Rather than clamping or silently accepting them, they are flagged, excluded from the ratio, and counted in `NCR Qty Anomalies` on the scorecard.

**Design limitations**
- **Supplier-linked ≠ supplier-responsible.** An NCR naming a supplier may be an internal or design fault. There is no responsibility field in the source.
- **Numerator and denominator have different scopes.** The numerator counts NCRs matched by exact Name+City; the denominator is *all* PO lines for that Name+City. The 51 unmatched NCRs (Section 10.1) leave their vendors' denominators intact — those vendors' rates are understated (D-03).
- **The denominator is PO lines, not receipts or received units.** A vendor with many small lines is measured more leniently per line than one with few large lines.
- **Absence reads as excellence.** A vendor with 5 PO lines and no matched NCR scores exactly 100 (D-02).
- The penalty factor of 5 makes the metric extremely steep at low volume: 1 NCR against 5 PO lines = 20% = score 0.

### 8.3 Lead-Time Performance — weight 15, minimum sample 5

Fully implemented; effectively inert on current data.

**Part-number key normalisation** (`_normalize_part_number`) handles the three ways Excel corrupts part numbers: floats that should be integers (`12345.0` → `"12345"`), values Excel coerced to dates, and whitespace/case variance. The original column is never modified — a separate `part_number_match_key` is added on both sides.

**Benchmark construction** (`_convert_lead_time` + conflict handling)

```
1. Coerce Item Master lead_time to numeric; date-like values → NaN
2. Drop rows with no usable part key
3. Count distinct lead times per part; if a part has more than one, DROP THE PART ENTIRELY
4. One row per part (first)
5. Left-merge onto PO with validate="many_to_one" + row-count assertion
```

Step 3 is the notable decision: where Item Master disagrees with itself, the design refuses to pick a winner rather than taking `first`, `min` or `mean`.

**Metrics**

```
actual_lead_time_days = last_receipt_date − order_date
lead_time_eligible    = fully_received AND both dates present AND item_lead_time_days present
                        AND actual >= 0 AND item >= 0
lead_time_variance    = actual − item                    (only where eligible)
lead_time_adherent    = actual <= item                   (only where eligible)
Lead-Time Adherence % = adherent_count / eligible_count × 100
```

**Coverage failure**

| Measure | Count |
|---|---|
| PO rows whose part exists in Item Master | 23,336 of 23,344 |
| PO rows receiving a usable Item Master lead time | **3** |
| Lead-time eligible PO rows | **3** |

Part matching works at 99.97%. The failure is entirely in the `Lead Time` column's *content* — empty, date-typed or conflicting. No vendor will ever reach the minimum sample of 5, so this component is permanently `INSUFFICIENT BENCHMARK DATA`, and its 15% weight is renormalized away for every vendor. **The scorecard is in practice a four-component instrument** (D-04).

### 8.4 Responsiveness Proxy — weight 15, minimum sample 3

**Explicitly a placeholder.** The source has no supplier request/response timestamps, so NCR resolution status stands in for responsiveness.

```
responsiveness_eligible = supplier_linked AND resolved NOT NULL
resolved_flag           = eligible AND resolved == True
unresolved_flag         = eligible AND resolved == False
Responsiveness Proxy %  = resolved_count / eligible_count × 100
```

**Current results (matched NCRs):** 327 eligible, 196 resolved, 131 unresolved.

**Design limitations**
- Measures NCR closure, not supplier response time. An NCR closed by Macrodyne after nine months is "resolved".
- Age-insensitive: a two-day resolution and a two-year resolution are identical.
- Depends on an unvalidated boolean contract (§5.3).
- Only *matched* NCRs contribute, so the same scope asymmetry as Quality applies.

**This is the most improvable component with zero new source data** — `created_date`, `target_date`, `date_follow_up`, `total_tasks` and `outstanding_tasks` are already in the extract and mapped (§15.4).

### 8.5 Commercial Performance — weight 20, minimum sample 5

**Comparison scope.** Prices are only ever compared within an identical tuple:

```
vendor_match_name + vendor_match_city + part + currency + UOM
```

This is what makes the metric defensible — no cross-currency, cross-part or cross-UOM comparison is arithmetically possible.

**Base eligibility**

```
commercial_base_eligible = vendor name, part, currency, UOM, order_date all present
                           AND unit_price present AND unit_price > 0
```

**Previous-price derivation**

```
sort by [group keys, order_date, original_row_order]
previous_unit_price = groupby(group_keys, dropna=False)["unit_price"].shift(1)
restore original row order
```

`_commercial_row_order` is captured before sorting and used both as a stable tiebreaker for same-day purchases and to restore the caller's row order afterwards, so this stage has no side effect on any other stage's row alignment.

**Classification and metrics**

```
price_comparison_eligible = base_eligible AND previous_unit_price present AND > 0
price_change_pct = (current − previous) / previous × 100
price_stable     = current <= previous        ← equality counts as stable
price_increased  = current >  previous
Price Stability % = price_stable_count / price_comparison_count × 100
```

**Current results:** 22,488 base-eligible, 10,975 comparisons, 9,356 stable/decreased, 1,619 increased.

**Design limitations** — the prototype does not account for quantity breaks, project-specific pricing, negotiated changes, commodity or FX inflation, purchasing strategy, or contractual terms. Comparisons are per PO line, so a single order split across lines inflates the comparison count. `currency_rate` is mapped but unused, so no common-currency view exists.

---

## 9. Scoring and Grading Engine

`src/scoring/vendor_scoring.py`. Pure functions over the aggregated vendor frame; no I/O except loading the rules.

### 9.1 Component scorer contract

Every `_score_*(row, rules)` returns `(score, status)`:

```
sample below minimum  → (NaN, "INSUFFICIENT …")
metric is NaN         → (NaN, "NO VALID … METRIC")
otherwise             → (clamp(metric, 0, 100), "SCORED")
```

The status string is carried onto the scorecard next to the score, so an `N/A` always states *why*. This is the design's answer to "the grade is blank and no one knows if that's good or bad."

`_safe_score` clamps to 0–100 and preserves `NaN`; `NaN` never becomes 0.

### 9.2 Grading

```python
_assign_grade(score, thresholds):
    NaN → "N/A"
    sort thresholds descending by value; return the first grade whose minimum <= score
```

Applied identically to all five component scores and to the overall score.

With `D: 0`, **every scored vendor receives at least a D.** There is no failing grade, and D spans 0–69 — half the scale in one bucket (D-11).

### 9.3 Overall score — weight renormalization

```python
for each component with a non-NaN score:
    weighted_score   += score × weight
    available_weight += weight
    available_components += 1

if available_components < 3 or available_weight == 0:
    overall = NaN,  status = "INSUFFICIENT COMPONENT COVERAGE"
else:
    overall = weighted_score / available_weight     # renormalized, gaps excluded
    status  = "SCORED"

weight_coverage_pct = available_weight / total_configured_weight × 100
```

**Worked example** — a vendor scored on Delivery 92, Quality 100, Commercial 85, with Lead-Time and Responsiveness `N/A`:

```
weighted        = 92×25 + 100×25 + 85×20 = 2,300 + 2,500 + 1,700 = 6,500
available_weight= 25 + 25 + 20 = 70
overall         = 6,500 / 70 = 92.86  → grade A
coverage        = 70 / 100 = 70%
```

Treating the gaps as zero would have produced 65.0 and a grade of D. The renormalization is the correct choice, and `Weight Coverage %` is published beside the score so a reader can see that an A rests on 70% of the intended evidence.

### 9.4 Coverage outcomes

| Measure | Count |
|---|---|
| Vendor+Location rows | 416 |
| Receiving an overall score | 106 |
| `INSUFFICIENT COMPONENT COVERAGE` | 310 |

Grade distribution: **A 19, B 31, C 25, D 31, N/A 310.**

Because Lead-Time is permanently unavailable, reaching 3 components requires Delivery + Quality + Commercial, or two of those plus Responsiveness. The minimum-3 rule is therefore much closer to "almost everything must be available" than it reads (D-20).

---

## 10. Exception Handling Design

The prototype produces two exception registers rather than resolving ambiguity silently. Both are workbook tabs, not log lines.

### 10.1 Unmatched supplier-linked NCRs — 51 records

NCR → vendor matching is an **exact equi-join on (`vendor_match_name`, `vendor_match_city`)** against the distinct vendor keys present in the PO aggregate, executed with `indicator=True`.

```
378 supplier-linked NCRs
├── 327 matched            → aggregated into the scorecard
└──  51 unmatched          → "Unmatched NCRs" worksheet
     ├── name+city present but no PO match
     └── location missing
```

No fuzzy matching, no name-only fallback. Stage 10 *computes* `po_location_count` — how many distinct PO locations exist for each vendor name — which would support a safe name-only fallback where a vendor has exactly one location. It is attached to the export for human review but never used to match (D-13). That restraint is defensible for a prototype and is a documented candidate for Phase 2 (§16).

The consequence is quantified in D-03: those 51 NCRs are absent from Quality numerators while their vendors' PO lines remain in the denominators.

### 10.2 Vendor Master review — 66 records

```
INCOMPLETE : any of vendor_name, address_line_1, postal_code null OR whitespace-only   → 58
EXACT DUPLICATE : COMPLETE rows sharing (name_key, address_key, postal_code_key)        →  8
review_required = INCOMPLETE OR exact_duplicate
```

Keys are `strip().upper()` on the pandas `string` dtype. Duplicate detection uses `keep=False`, so **all** members of a duplicate group are flagged, not just the later ones. Duplicate detection is restricted to `COMPLETE` rows — a null address would otherwise make unrelated vendors collide.

`review_reason` is assigned by two sequential `.loc` writes with `EXACT DUPLICATE` applied last. That precedence is never exercised: because duplicate detection is confined to `COMPLETE` rows, no record can be both incomplete and flagged duplicate. The two sets are disjoint by construction, which is also why 58 + 8 = 66 reconciles exactly. If duplicate detection is ever widened to incomplete records (Phase 2), single-reason reporting becomes a real limitation.

`identify_partial_duplicates`, `find_possible_vendor_matches` and `calculate_similarity` (a `SequenceMatcher` ratio) implement fuzzy vendor consolidation but are **not imported by `main.py`** — unreachable in the current pipeline (D-13, D-14).

---

## 11. Output Design — Excel Workbook

`data/output/Vendor_Scorecard_Prototype.xlsx`, five worksheets, written through one `pd.ExcelWriter` context. `fullCalcOnLoad`, `forceFullCalc` and `calcMode="auto"` are set (guarded by `try/except AttributeError` for openpyxl version variance) so the formula-driven detail sheet recalculates on open.

### 11.1 Sheet 1 — Vendor Scorecard

All 416 rows. ~50 columns in a fixed presentation order chosen so a reader meets the conclusion before the evidence:

```
Identity      Vendor Key | Vendor | Location
Overall       Score | Grade | Weight Coverage % | Scored Components | Overall Status
Activity      PO Transactions | Distinct POs | Total Ordered Qty | Total Received Qty
Delivery      Eligible | On-Time | Late | OTD % | Avg Days Late | Score | Grade | Status
Quality       Supplier-Linked NCRs | NCR Rate % | Quality-Eligible NCRs | Valid NCR Qty
              | Valid Rejected Qty | NCR Rejected % | Qty Anomalies | Score | Grade | Status
Lead-Time     Eligible | Adherent | Avg Actual | Avg Variance | Adherence % | Score | Grade | Status
Responsive.   Eligible NCRs | Resolved | Unresolved | Resolution % | Score | Grade | Status
Commercial    Comparisons | Stable/Decreased | Increased | Avg Price Change % | …
```

Every component block follows the identical **evidence → metric → score → grade → status** rhythm, so the sheet is scannable across components without re-learning the layout.

`Vendor Key` is `vendor_match_name | vendor_match_city` with `"UNKNOWN VENDOR"` and `"NO LOCATION"` fallbacks — it is both the human label and the lookup key used by Sheet 2.

`_select_existing_columns` filters the mapping to columns actually present, so a removed metric degrades the sheet instead of raising `KeyError`. The same helper silently hides genuine mapping bugs — see D-07.

Formatting: frozen header, autofilter, banded fills, per-grade cell colouring (`_apply_grade_style`: A green, B blue, C gold, D red, N/A grey), and width capping.

### 11.2 Sheet 2 — Vendor Detail

An interactive single-vendor view.

- `B3` holds a data-validation dropdown sourced from `$Z$2:$Z$n`, a hidden column of every Vendor Key.
- Default selection is the highest-scoring vendor, falling back to the first key.
- All displayed *metric* values — metric, sample, score, grade, status — are Excel `INDEX/MATCH` formulas against Sheet 1, so **the card is live**, not a rendered snapshot: changing the dropdown refreshes it without Python. Component labels, the "Configured Weight" column and the interpretation notes are static text (D-22).
- Layout: title band → selector → Prototype Overall Assessment (vendor, location, PO transactions, scored components, score, grade, weight coverage, status) → a component table from row 11 (Component | Configured Weight | Primary Metric | Sample | Score | Grade | Status) → supporting metrics → interpretation notes.
- Conditional formatting drives grade colours so they follow the dropdown.

This design deliberately puts sample size and status *next to* every score, so a user reading a single vendor cannot see an A without also seeing what it was computed from.

### 11.3 Sheet 3 — Prototype Notes

Documents the "not approved policy" warning, component weights and definitions, grade thresholds, the overall-score rule, assumptions and current data limitations — so that a workbook circulated on its own still carries its own caveats.

**The sheet is only partly generated.** `_build_prototype_notes_sheet` reads exactly two values from `scorecard_rules.json`: `grade_thresholds` and `overall.minimum_available_components`. Everything else is a literal in the exporter — the warning text, the component names, weights and metric definitions, the overall-score prose, and the data-limitations bullets (one of which hardcodes "51 supplier-linked NCR records", a figure that goes stale the moment the data is refreshed). **Minimum sample sizes are not documented on the sheet at all.**

The intent — configuration and its documentation emitted from one source — is right; the implementation is two-thirds hand-typed and will drift (D-22).

### 11.4 Sheet 4 — Unmatched NCRs (51)

The `fallback_check` frame — unmatched NCRs plus the `po_location_count` diagnostic — exported in full for triage.

### 11.5 Sheet 5 — Vendor Review (66)

`review_required` Vendor Master rows: Company ID, Vendor Name, Address, City, Province/State, Postal Code, Data Status, Exact Duplicate, Review Reason.

**Defect:** the mapping requests `province`, but `column_mappings.json` produces `state_province`. `_select_existing_columns` drops it without error, so the Province/State column never appears (D-07).

---

## 12. Cross-Cutting Concerns

**Error handling.** Two modes. Load failures raise `ValueError`, are caught per-dataset in `main.py`, set `has_errors`, and stop the pipeline before any processing. Everything after Stage 0 is unguarded — an exception there produces a raw traceback and no output. The process exit code is 0 in every case, including validation failure, which makes the pipeline unsafe to schedule as-is.

**Observability.** ~60 `print` blocks: source counts, eligibility counts, match reconciliation, coverage tables, grade distributions, a full 20-row preview, a worked single-vendor example, and final control totals. As a development instrument this is excellent — the reconciliation blocks (`before merge` / `after merge`, `matched + unmatched + missing = total`) are real controls. As production telemetry it is unusable: no timestamps, no levels, no machine-readable form, no persistence.

**Determinism.** Deterministic given identical inputs. The lead-time module pins `sort=False` explicitly where ordering matters; the other three groupbys (`vendor_aggregator`, `ncr_aggregator`, and Stage 10's `po_location_count`) rely on pandas' default `sort=True`, which is deterministic but is an implicit dependency rather than a stated one. The commercial stage captures and restores row order around its own sort, so it has no effect on any other stage's alignment. Floating-point aggregation order is stable.

**Idempotence.** Re-running overwrites the same workbook. There is no run identifier, no as-of date and no history, so two runs cannot be compared and a prior result cannot be reproduced after the inputs are refreshed (D-09).

**Evaluation period.** There is none. Every metric pools the entire PO and NCR history in the extract. A supplier that was poor two years ago and excellent since is indistinguishable from one that has been mediocre throughout, and the score changes shape every time the extract's history depth changes (D-05).

**Performance.** Adequate: minutes, dominated by Excel I/O. The five `apply(axis=1)` scoring passes are row-wise Python over 416 rows — trivial here, and worth vectorising only if the grain moves to part or PO level.

**Security and data handling.** `.gitignore` excludes `data/input/*` and `data/output/*` (with `.gitkeep` exceptions), Excel lock files, `.env`, and IDE/OS noise. No credentials exist in the codebase today; the ETO migration introduces them and will need a secret-management decision before Phase 3. Source data lives in OneDrive under the user's account; the workbook contains supplier-identifying commercial data and should be treated as internal-confidential.

**Testing.** None. No test directory, no fixtures, no CI. Correctness currently rests on printed control totals read by a human. Given that the scoring rules are pure functions over a DataFrame, they are unusually easy to unit-test (§16 Phase 1).

---

## 13. Known Defects, Risks and Design Debt

Findings from code inspection, ordered by severity. Each is stated as an observation with its consequence.

### High

**D-01 — Vendor identity is derived from free text while stable keys sit unused.**
The scorecard grain comes from regex-parsing the PO `Supplier` string. Meanwhile `supplier_number` (PO `Supplier #`) is mapped on every PO row and never read, and Vendor Master `company_id` (ETO `CompanyID`) is loaded and used only for display. Any change to the `NAME [CITY] (APPROVED)` convention silently re-partitions the scorecard; a vendor with two spellings becomes two rows with split history.
*Consequence:* the identity spine of the entire product is a formatting convention. This is the highest-value fix available and it requires no new data — only confirmation of how `Supplier #` relates to `CompanyID`.

**D-02 — Absence of NCRs is scored as perfect quality.**
Stage 12 fills missing NCR counts with 0; `_score_quality` gates only on PO activity (≥5 lines). A vendor with 5 PO lines and no matched NCR scores 100 and grades A, indistinguishable from a vendor with 500 lines and a verified zero-defect record. This inflates the top of the graded population — the 106 vendors that actually receive an overall score — and, because Quality carries the joint-highest weight, a spurious 100 there can lift a vendor a full grade.
*Consequence:* the design's own "missing data must not look like good performance" principle is violated in the one component where it matters most. Fix: a `minimum_quality_evidence` gate (received lines or received quantity), and/or a distinct `NO QUALITY EVIDENCE` status separate from `SCORED`.

**D-03 — Quality numerator and denominator have different scopes.**
The numerator counts only exactly-matched NCRs; the denominator counts all PO lines for the vendor key. The 51 unmatched NCRs are therefore removed from their vendors' rates while those vendors' PO volume remains.
*Consequence:* systematically understated NCR rates for exactly the vendors whose data is messiest. Fix alongside D-01 (better matching) or by excluding affected vendors from Quality scoring until triaged.

**D-04 — Lead-Time is structurally unavailable.**
3 of 23,344 PO rows are lead-time eligible; the minimum sample is 5. The component can never score, and its 15% weight is renormalized away for every vendor.
*Consequence:* the published five-component design is a four-component instrument in practice. Either source a real benchmark (§15.4) or remove the component from the published weighting until one exists — carrying a permanently-N/A component makes coverage figures harder to interpret.

**D-05 — No evaluation period.**
All metrics pool the full history in the extract. There is no `as_of_date`, no rolling window, no period column.
*Consequence:* scores are not comparable across runs, cannot show trend, and shift whenever the extract's depth changes. This blocks the primary business use of a scorecard — "is this supplier improving?"

### Medium

**D-06 — Only Purchase Orders are type-validated.** Items, NCRs and Vendors get column-presence checks only. The `resolved` field's boolean contract is unguarded: a `"Yes"`/`"No"` extract would zero every vendor's responsiveness silently.

**D-07 — Vendor Review sheet silently drops Province/State.** `_build_vendor_review_sheet` maps `"province"`; the loader produces `state_province`. `_select_existing_columns` removes it without error. The resilience helper is masking a real bug — it should log what it drops.

**D-08 — Vendor Master quality findings never reach the scorecard.** 66 flagged records are reported in a separate tab, but a scored vendor row carries no indication that its master data is incomplete or duplicated.

**D-09 — No score history or run identity.** The workbook is overwritten each run. No `run_id`, no `as_of_date`, no persisted results, so no trend, no audit trail, no reproducibility.

**D-10 — `main.py` is a 1,825-line module-level script.** No `if __name__ == "__main__"` guard, no functions, no return codes; importing it executes the whole pipeline. The entire body sits in one `else:` block. Not testable, not importable, not schedulable safely.

**D-11 — No failing grade.** `D: 0` means every scored vendor gets at least a D, and D spans 0–69. A score of 12 and a score of 69 grade identically.

**D-12 — `rejected_purchase_orders` is captured and never surfaced.** Rows failing required-field checks are stored on the repository instance and printed as a count, but never exported. Invalid source rows leave no reviewable trail.

### Low

**D-13 — Dead code.** `identify_partial_duplicates`, `find_possible_vendor_matches` and `calculate_similarity` are unreachable from `main.py`. `excel_exporter.lookup_formula` contains a duplicate implementation after its `return`. Stage 10's `po_location_count` fallback logic is computed and exported but never used to match.

**D-14 — `calculate_similarity` will raise on null input.** `SequenceMatcher` receives `pd.NA` from `vendor_name_key` / `address_key` without a guard. Currently unreachable (D-13), but a latent failure if that path is enabled.

**D-15 — `fully_received` is computed twice**, identically, in `delivery_evaluator` and `vendor_aggregator`. Two definitions of one business rule that can diverge.

**D-16 — Unpinned dependencies, no lockfile, no tests, no CI.** `requirements.txt` is two unversioned lines. A pandas release changing `groupby(dropna=)` or `shift` semantics would alter results without warning.

**D-17 — Vendor name normalisation is bracket-position-sensitive.** Only a trailing `[...]` is stripped from the name, but the *first* `[...]` is extracted as the city. A mid-string bracket desynchronises the two.

**D-18 — Commercial comparisons are line-level and treat equality as stability.** A PO split across lines inflates comparison counts; a flat price scores identically to a reduction; no quantity-break or FX normalisation (`currency_rate` is mapped but unused).

**D-19 — "Average Days Late" is a mean over late lines only.** The label on the scorecard reads as an average over all deliveries. Correct as computed, misleading as labelled.

**D-20 — The minimum-3-components rule is stricter than it reads.** With Lead-Time permanently unavailable, 3 of 4 remaining components must score — closer to "nearly everything must be present" than to a lenient floor.

**D-21 — Hardcoded relative paths.** `data/input`, both config paths and the output path are relative to the current working directory, so the pipeline only runs from the repository root.

**D-22 — The workbook's own documentation is hand-typed, not generated.** Component weights appear as literal strings in both the Vendor Detail table and Prototype Notes; the quality penalty factor and the count of unmatched NCRs are literal prose; minimum sample sizes are documented nowhere on the sheet. Only grade thresholds and the minimum-component count are read from `scorecard_rules.json`. A weight change in JSON silently produces a workbook that documents the previous policy — the exact failure the config/code separation was designed to prevent.

**D-23 — Computed-and-discarded aggregates.** `received_transaction_count`, `fully_received_count` and `total_rejected_qty` are aggregated per vendor but appear in no export mapping and are read by nothing. Same pattern as D-12: work performed, result unreachable.

---

## 14. Open Business Decisions

Nothing in Sections 8 and 9 is approved policy. These must be settled with IT and Supply Chain before production.

| # | Decision | Currently | Depends on |
|---|---|---|---|
| 1 | Component weights | 25/25/15/15/20 | Business priority |
| 2 | Grade thresholds | A 90 / B 80 / C 70 / D 0 | Whether a failing grade exists |
| 3 | Minimum sample sizes | 5/5/5/3/5 | Statistical comfort vs coverage |
| 4 | OTD target-date rule | Revised, else Required | Whether Revised reflects supplier-caused change |
| 5 | Partial receipts in OTD | Excluded entirely | Whether a partial receipt is on-time |
| 6 | OTD tolerance window | None (1 day late = late) | Commercial expectation |
| 7 | Lead-Time benchmark source | Item Master (unusable) | PO promise date? Contract? Vendor quote? |
| 8 | Supplier responsibility on NCRs | Not determined; linkage used | Whether ETO holds a responsibility flag |
| 9 | Quality denominator | PO line count | Receipts? Received units? PPM? |
| 10 | Responsiveness definition | NCR resolution % | Which NCR timestamps are authoritative |
| 11 | Commercial methodology | Line-level price stability | Quantity breaks, FX, negotiated change |
| 12 | Vendor identity key | Parsed `Supplier` string | `Supplier #` ↔ `CompanyID` relationship |
| 13 | Evaluation period | All history | Rolling 12 months? Fiscal quarter? |
| 14 | Treatment of the 51 unmatched NCRs | Held as exceptions | Triage decision |
| 15 | Treatment of the 66 vendor exceptions | Reported only | Master-data cleanup ownership |

---

## 15. Target-State Design

### 15.1 Architecture

```
        ETO (SQL Server, system of record)
                     │  read-only
                     ▼
        reporting schema — base views
        (vw_purchase_order_line, vw_receipt_event,
         vw_ncr, vw_item, vw_vendor)
                     │
                     ▼
        SqlRepository  ──implements──►  VendorScorecardRepository
                     │
                     ▼
        Metric layer  (evaluation + aggregation — UNCHANGED)
                     │
                     ▼
        Scoring engine (config-driven, versioned ruleset)
                     │
        ┌────────────┴────────────┐
        ▼                         ▼
  scorecard schema          Excel exporter
  (persisted history)       (unchanged)
        │
        ▼
  Power BI / Project Console
```

The metric, aggregation and scoring layers move across untouched. The migration is confined to a new repository implementation, a persistence layer, and the reporting views behind them.

### 15.2 Reporting views (`reporting` schema)

Read-only, one per source concept, shaped to the column contract already declared in `column_mappings.json` so the mapping file can be reused verbatim.

| View | Purpose | Change vs today |
|---|---|---|
| `vw_purchase_order_line` | PO lines with vendor key | Adds `vendor_id` (CompanyID) — resolves D-01 |
| `vw_receipt_event` | One row per receipt transaction | **New capability** — resolves partial receipts and true OTD |
| `vw_ncr` | NCRs with supplier responsibility and task timestamps | Adds responsibility flag and dates — resolves D-08 items 8, 10 |
| `vw_item` | Item Master | Same |
| `vw_vendor` | Vendor Master keyed by CompanyID | Enables the join that does not exist today |

### 15.3 Persistence (`scorecard` schema)

```sql
scorecard_run              -- run_id, as_of_date, period_start, period_end,
                           -- ruleset_version, source_row_counts, started_at,
                           -- completed_at, status
dim_vendor                 -- vendor_id (CompanyID), name, location, active,
                           -- master_data_status
fact_vendor_metric         -- run_id, vendor_id, component, metric_name,
                           -- numerator, denominator, metric_value, sample_size
fact_vendor_component_score-- run_id, vendor_id, component, score, grade,
                           -- status, weight_applied
fact_vendor_overall_score  -- run_id, vendor_id, overall_score, overall_grade,
                           -- scored_component_count, weight_coverage_pct, status
exception_ncr_unmatched    -- run_id, ncr_number, vendor_text, reason, triage_status
exception_vendor_review    -- run_id, vendor_id, reason, triage_status
ruleset_version            -- version, effective_from, rules_json, approved_by,
                           -- approved_at
```

Storing numerator, denominator and sample size beside every metric — not just the percentage — is what makes a score explainable a year later and lets Power BI re-aggregate across periods without re-running Python. Stamping `ruleset_version` on every run means a weight change is a new version, not a rewrite of history.

### 15.4 Metric redesign (production candidates)

| Component | Today | Production candidate | Needs |
|---|---|---|---|
| **On-Time Delivery** | Last receipt vs target, fully-received only | Receipt-event level, quantity-weighted, agreed tolerance window, partial receipts handled | `vw_receipt_event` |
| **Quality** | Matched-NCR count / PO lines × 5 | Supplier-responsible NCRs / received lines; PPM on received vs rejected quantity as severity | Responsibility flag; receipt quantities |
| **Lead-Time** | Item Master `Lead Time` (3 usable rows) | PO promise date, contractual lead time, or vendor-quoted lead time on the PO | Decision 7; a populated benchmark |
| **Responsiveness** | NCR resolved yes/no | **Days from NCR creation to first supplier follow-up; on-time closure vs NCR target date; outstanding-task ageing** | *Nothing new* — `created_date`, `target_date`, `date_follow_up`, `total_tasks`, `outstanding_tasks` are already mapped |
| **Commercial** | Line-level price stability | Spend-weighted price index, currency-normalised via `currency_rate`, quantity-break aware, negotiated changes excluded | Decision 11; `currency_rate` (already mapped) |

**The Responsiveness row is the cheapest material improvement in this document.** The component is documented as blocked on source data that is, in fact, already in the extract and already in the column mapping.

### 15.5 Consumption

- **Excel** — the existing exporter, unchanged, for supplier review packs.
- **Power BI** — direct on the `scorecard` schema: trend by vendor and component, distribution by grade, exception ageing, coverage over time.
- **Project Console** — the same data is a natural fit for the existing console's reporting patterns if a supplier view is wanted in-app.

---

## 16. Migration and Refactor Plan

Phased so that each phase is independently useful and none blocks the business review.

### Phase 0 — Stabilise (no behaviour change)

1. Wrap `main.py` in functions with `if __name__ == "__main__"`; return a non-zero exit code on validation failure.
2. Replace `print` with `logging` at INFO, preserving every control total; keep reconciliation blocks as structured records.
3. Pin `requirements.txt`; add a lockfile.
4. Add unit tests for `vendor_scoring` (pure functions, highest value per test), the two vendor-matcher regexes, and the four evaluators against small fixtures.
5. Fix D-07 (`province` → `state_province`) and make `_select_existing_columns` log dropped columns.
6. Remove or quarantine dead code (D-13); guard `calculate_similarity` (D-14).
7. Collapse the duplicate `fully_received` definition (D-15).
8. Export `rejected_purchase_orders` as a sixth worksheet (D-12); either surface or remove the computed-and-discarded aggregates (D-23).
9. Make paths configurable via CLI or environment (D-21).
10. Drive the Vendor Detail weights column and the whole Prototype Notes sheet from `scorecard_rules.json`, including minimum sample sizes, and replace the hardcoded "51 unmatched NCRs" with the run's actual count (D-22).

### Phase 1 — Trust the numbers

11. Add an `as_of_date` and a configurable evaluation window (D-05); stamp both on every run and in the workbook header.
12. Add a Quality evidence gate and a distinct status for "no quality evidence" (D-02).
13. Publish per-metric numerator, denominator and sample size in the export.
14. Decide and implement the Quality scope asymmetry fix (D-03).
15. Add a `ruleset_version` to `scorecard_rules.json` and echo it in the Prototype Notes sheet.

### Phase 2 — Identity and matching

16. Confirm the `Supplier #` ↔ `CompanyID` relationship; switch the scorecard grain to a stable vendor key (D-01) with the parsed name retained as a display label.
17. Join Vendor Master to the scorecard and surface master-data status on scored rows (D-08).
18. Enable the safe name-only NCR fallback where a vendor name has exactly one PO location — the `po_location_count` diagnostic is already built (D-13).
19. Re-triage the 51 unmatched NCRs against the improved key.

### Phase 3 — Database integration

20. Build the `reporting` views (§15.2), starting with `vw_receipt_event`.
21. Implement `SqlRepository` against the existing ABC; run both repositories in parallel and reconcile row-for-row.
22. Build the `scorecard` schema and persist every run (§15.3).
23. Decommission the Excel input layer; keep the Excel *output*.

### Phase 4 — Production metrics and consumption

24. Implement the redesigned metrics (§15.4) once decisions 4–11 are signed off, starting with Responsiveness (no new data required).
25. Build Power BI trend and exception reporting on the persisted history.
26. Schedule the run; alert on validation failure and on coverage regressions.

**Suggested sequencing:** Phase 0 is a few days and unblocks everything else. Phases 1 and 2 can proceed in parallel with the business review, since they improve honesty rather than change policy. Phase 3 should not start until the `Supplier #` ↔ `CompanyID` question (Decision 12) is answered, because it determines the shape of every view.

---

## Appendix A — Derived Field Dictionary

**Purchase Orders** (added by Stages 2, 3, 5, 6)

| Field | Stage | Definition |
|---|---|---|
| `vendor_match_name` | 2 | Supplier string, uppercased, trailing `[...]` / `(APPROVED)` stripped |
| `vendor_match_city` | 2 | First `[...]` group, uppercased |
| `target_date` | 3 | `revised_date` else `required_date` |
| `fully_received` | 3 | `ordered_qty > 0 AND received_qty >= ordered_qty` |
| `delivery_eligible` | 3 | `fully_received AND target_date AND last_receipt_date` |
| `on_time` / `late` | 3 | eligible AND receipt `<=` / `>` target |
| `days_late` | 3 | `max(receipt − target, 0)` where eligible |
| `late_days_only` | 3 | `days_late` where late |
| `part_number_match_key` | 5 | Normalised part key (numeric/date/whitespace safe) |
| `item_lead_time_days` | 5 | Item Master benchmark, conflicts excluded |
| `actual_lead_time_days` | 5 | `last_receipt_date − order_date` |
| `lead_time_eligible` | 5 | receipt complete, dates present, benchmark present, both `>= 0` |
| `lead_time_variance_days` | 5 | `actual − benchmark` where eligible |
| `lead_time_adherent` | 5 | `actual <= benchmark` where eligible |
| `eligible_actual_lead_time_days` | 5 | `actual` where eligible |
| `commercial_part_key` / `_currency_key` / `_uom_key` | 6 | Trimmed, uppercased grouping keys |
| `commercial_base_eligible` | 6 | Vendor, part, currency, UOM, date present; price `> 0` |
| `previous_unit_price` | 6 | `shift(1)` within group, ordered by date then row order |
| `price_comparison_eligible` | 6 | base-eligible AND previous price present and `> 0` |
| `price_change_pct` | 6 | `(current − previous) / previous × 100` |
| `price_stable` / `price_increased` | 6 | `current <= previous` / `current > previous` |

**NCRs** (Stage 7): `vendor_match_name`, `vendor_match_city`, `supplier_linked`, `ncr_quantity_anomaly`, `quality_eligible`, `quality_quantity`, `quality_rejected_quantity`, `responsiveness_eligible`, `resolved_flag`, `unresolved_flag`.

**Vendors** (Stage 1): `vendor_quality_status`, `vendor_name_key`, `address_key`, `postal_code_key`, `exact_duplicate_flag`, `review_required`, `review_reason`.

**Vendor summary** (Stages 8, 11, 13): 18 PO aggregates (plus the two group keys) + `on_time_delivery_pct`, `lead_time_adherence_pct`, `price_stability_pct`; 9 NCR aggregates + `ncr_rejected_pct`, `responsiveness_proxy_pct`; then `supplier_linked_ncr_rate_pct`, five `*_prototype_score`, five `*_score_status`, five `*_prototype_grade`, `prototype_scored_component_count`, `prototype_weight_coverage_pct`, `prototype_overall_score`, `prototype_overall_status`, `prototype_overall_grade`.

## Appendix B — Status Vocabulary

| Component | Statuses |
|---|---|
| Delivery | `SCORED`, `INSUFFICIENT SAMPLE`, `NO VALID DELIVERY METRIC` |
| Quality | `SCORED`, `INSUFFICIENT PO SAMPLE` |
| Lead-Time | `SCORED`, `INSUFFICIENT BENCHMARK DATA`, `NO VALID LEAD-TIME METRIC` |
| Responsiveness | `SCORED`, `INSUFFICIENT RESPONSE EVENTS`, `NO VALID RESPONSIVENESS METRIC` |
| Commercial | `SCORED`, `INSUFFICIENT PRICE HISTORY`, `NO VALID COMMERCIAL METRIC` |
| Overall | `SCORED`, `INSUFFICIENT COMPONENT COVERAGE` |
| Vendor data | `COMPLETE`, `INCOMPLETE` |
| Vendor review reason | `MISSING REQUIRED VENDOR DATA`, `EXACT DUPLICATE` |

Note that Quality has no "no valid metric" status — because a missing NCR count is filled with zero, it always produces a number (D-02).

## Appendix C — Mapped-but-Unused Source Fields

Declared in `column_mappings.json`, loaded on every run, never read by any module. These represent available headroom, and each is a column the source extract must keep supplying for no current benefit.

**Purchase Orders:** `project_number`, `machine_code`, `po_part_number`, `currency_rate`, `supplier_number`, `order_number`, `receiving_date`, `extended_value` *(type-validated only)*.

**NCRs:** `project_number`, `machine_code`, `title`, `origin`, `total_tasks`, `outstanding_tasks`, `released`, `source_info`, `po_number`, `part_number`, `interim_action`, `root_cause`, `corrective_pre_action`, `ncr_costs`, `ncr_hours`, `target_date`, `date_follow_up`, `created_date`, `item_id`.

**Item Master:** `description`, `uom`, `category`, `list_price`, `revision`, `lpp`, `quantity_on_hand`, `preferred_supplier`, `supplier_part_number`, `last_supplier`, `manufacturer`, `manuf_part_number`, `quantity_reserved`.

**Vendors:** `address_line_2`, `country`, and `state_province` *(requested by the review sheet under the wrong name and therefore never rendered — D-07; its only other reference is inside unreachable code, D-13)*. `company_id`, `city` and `postal_code` reach the Vendor Review sheet as display columns only; none participates in a join.

Three of these are load-bearing for the improvements above: **`supplier_number`** (D-01, stable vendor identity), the **NCR date and task fields** (§15.4, real responsiveness), and **`currency_rate`** (§15.4, common-currency commercial view). `receiving_date` and `po_number`/`part_number` on the NCR side are also worth revisiting — the former may support receipt-level OTD ahead of `vw_receipt_event`, the latter may support NCR-to-PO matching that does not depend on parsing a vendor name at all.
