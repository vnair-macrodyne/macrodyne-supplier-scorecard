Vendor Scorecard Prototype

Overview

This project is a working prototype for a Macrodyne Vendor Scorecard. It consolidates purchasing, NCR, Item Master, and Vendor Master data into a vendor-level scorecard with five performance components:

On-Time Delivery

Quality / NCR

Lead-Time Performance

Responsiveness Proxy

Commercial Performance

The current output includes vendor-level metrics, prototype component scores and grades, an overall prototype score and grade, an individual Vendor Detail view, and exception/review worksheets.

Important: This is a prototype only. Weights, thresholds, grading rules, minimum sample sizes, and proxy definitions require business review and approval before production use.

Current Architecture

ETO / Excel source data
        |
        v
Data Access Layer
        |
        v
Validation / Data Quality
        |
        v
Vendor Matching
        |
        v
Metric Evaluation
        |
        v
Vendor Aggregation
        |
        v
Prototype Scoring / Grading
        |
        v
Excel Vendor Scorecard

The longer-term target architecture is to use ETO as the system of record and replace the Excel input layer with read-only database queries or reporting views.

Project Structure

Vendor Scorecard Project/
|
|-- config/
|   |-- column_mappings.json
|   |-- sources.json
|   |-- scorecard_rules.json
|
|-- data/
|   |-- input/
|   |   |-- Complete Vendors List 1.xlsx
|   |   |-- ItemMaster.xlsx
|   |   |-- PO STATUS_TEST.xlsx
|   |   `-- NCR_STATUS.xlsx
|   |
|   `-- output/
|       `-- Vendor_Scorecard_Prototype.xlsx
|
|-- src/
|   |-- aggregation/
|   |   |-- vendor_aggregator.py
|   |   `-- ncr_aggregator.py
|   |
|   |-- data_access/
|   |   |-- base_repository.py
|   |   `-- excel_repository.py
|   |
|   |-- evaluation/
|   |   |-- delivery_evaluator.py
|   |   |-- lead_time_evaluator.py
|   |   |-- commercial_evaluator.py
|   |   `-- ncr_evaluator.py
|   |
|   |-- matching/
|   |   `-- vendor_matcher.py
|   |
|   |-- quality/
|   |   `-- vendor_quality.py
|   |
|   |-- reporting/
|   |   `-- excel_exporter.py
|   |
|   `-- scoring/
|       `-- vendor_scoring.py
|
|-- main.py
|-- requirements.txt
`-- README.md

How to Run

From the project root:

python main.py

The pipeline validates the required datasets, processes the five components, applies prototype scoring rules, and creates:

data/output/Vendor_Scorecard_Prototype.xlsx

Current Data Validation Results

Latest validated source counts:

Item Master: 86,730 rows

Purchase Orders: 23,344 valid rows

NCRs: 1,248 rows

Vendor Master: 1,803 rows

Vendor Master review findings:

58 records missing required vendor data

8 records identified as exact duplicates

66 total Vendor Master records requiring review

Operational PO vendor aggregation currently produces:

416 Vendor + Location scorecard rows

Supplier-linked NCR reconciliation:

378 supplier-linked NCRs

327 matched to PO Vendor + Location

51 retained as unmatched exceptions

Scorecard Components

1. On-Time Delivery

Prototype Metric

On-Time Delivery Percentage:

On-Time Eligible PO Rows
------------------------ x 100
Delivery Eligible PO Rows

A PO row is currently delivery-eligible when:

the PO is fully received,

a usable target date exists, and

a Last Receipt Date exists.

Target Date Rule

Prototype logic:

Revised Date if available
otherwise Required Date

Current Results

Delivery eligible PO rows: 20,181

On-time: 13,164

Late: 7,017

Limitation

The current source uses the aggregate Last Recd Date. Detailed receipt-event history has not yet been incorporated.

2. Quality / NCR

Prototype Scoring Metric

Supplier-Linked NCR Rate:

Supplier-Linked NCR Count
------------------------- x 100
PO Transaction Count

Prototype Quality Score:

100 - (Supplier-Linked NCR Rate % x 5)

The multiplier of 5 is a prototype penalty factor stored in config/scorecard_rules.json.

Supporting Quality Metrics

The workbook also reports:

Supplier-Linked NCR Count

Supplier-Linked NCR Rate %

Quality-Eligible NCR Count

Valid NCR Quantity

Valid Rejected Quantity

NCR Rejected %

NCR Quantity Anomaly Count

NCR Rejected % is treated as a severity indicator and does not directly drive the current prototype Quality Score.

Important Limitation

A supplier being linked to an NCR does not necessarily mean the supplier has been formally confirmed as responsible.

The current denominator is PO transaction activity, not received units. A future production metric may use confirmed supplier responsibility and receipt quantities.

3. Lead-Time Performance

Prototype Metric

Actual Lead Time:

Last Receipt Date - PO Date

Prototype Lead-Time Adherence compares Actual Lead Time against Item Master Lead Time.

Current Data Limitation

The lead-time calculation logic is implemented, but current source coverage is insufficient:

23,336 PO rows have Part Numbers found in Item Master

Only 3 PO rows receive a usable Item Master Lead Time

Only 3 PO rows are currently lead-time eligible

Because of this, Lead-Time is shown as N/A for reliable vendor scoring and is excluded from overall scores when unavailable.

This is a source-data coverage issue, not a PO-to-Item matching issue.

4. Responsiveness Proxy

Prototype Metric

Resolved Supplier-Linked NCRs
----------------------------- x 100
Responsiveness-Eligible NCRs

Current matched NCR population:

Responsiveness-eligible matched NCRs: 327

Resolved: 196

Unresolved: 131

Important Limitation

This is a prototype proxy only.

The current data does not contain confirmed supplier request/response timestamps, so NCR resolution status is being used as an interim indicator.

It should not be interpreted as actual supplier response time.

5. Commercial Performance

Prototype Metric

Commercial performance currently uses Price Stability %.

Price comparisons are only made within the same:

Vendor
+ Location
+ Part
+ Currency
+ UOM

A comparison is classified as price-stable when:

Current Unit Price <= Previous Comparable Unit Price

Current Results

Commercial base-eligible PO rows: 22,488

Repeat price comparisons: 10,975

Price stable / decreased: 9,356

Price increased: 1,619

Supporting Metrics

Price Comparison Count

Price Stable / Decreased Count

Price Increase Count

Average Price Change %

Price Stability %

Important Limitation

The prototype does not yet account for:

quantity breaks,

project-specific pricing,

negotiated commercial changes,

inflation,

purchasing strategy,

contractual terms.

It also intentionally avoids summing raw commercial values across different currencies.

Prototype Scoring and Grading

Component Weights

Current prototype configuration:

Component

Weight

On-Time Delivery

25%

Quality / NCR

25%

Lead-Time Performance

15%

Responsiveness Proxy

15%

Commercial Performance

20%

These values are stored in:

config/scorecard_rules.json

They are configurable and are not approved production weights.

Grade Thresholds

Current prototype grading:

Grade

Minimum Score

A

90

B

80

C

70

D

0

Minimum Sample Rules

Prototype rules currently include minimum sample requirements before a component may be scored.

Examples:

Delivery: minimum 5 eligible deliveries

Quality: minimum 5 PO transactions

Lead-Time: minimum 5 eligible lead-time transactions

Responsiveness: minimum 3 eligible NCR resolution events

Commercial: minimum 5 repeat price comparisons

If a component does not meet its minimum sample requirement, it is shown as N/A.

Overall Prototype Score

A vendor must have at least:

3 scored components

to receive an Overall Prototype Score.

Unavailable components are not treated as zero.

Instead, the configured weights of available components are normalized across the components that can actually be scored.

The workbook also reports:

Scored Component Count

Weight Coverage %

Overall Status

This prevents missing source data from automatically penalizing a vendor.

Latest Prototype Grade Coverage

Current overall scoring coverage:

106 Vendor + Location records receive an Overall Prototype Score

310 remain N/A because of insufficient component coverage

Latest overall prototype grade distribution:

A: 19

B: 31

C: 25

D: 31

N/A: 310

These grades are for prototype demonstration only.

Excel Workbook

The generated workbook contains the following worksheets.

Vendor Scorecard

Organization-wide scorecard covering all 416 Vendor + Location records.

Includes:

Activity volumes

Delivery metrics / grade

Quality metrics / grade

Lead-Time metrics / grade

Responsiveness proxy metrics / grade

Commercial metrics / grade

Prototype Overall Score

Prototype Overall Grade

Weight Coverage %

Component scoring statuses

Vendor Detail

Interactive individual-vendor scorecard.

Use the dropdown at the top of the worksheet to select:

Vendor | Location

The sheet then displays:

Overall Score and Grade

Weight Coverage

Component metrics

Component scores

Component grades

Component statuses

Supporting metrics

Prototype interpretation notes

Unavailable metrics are shown as N/A rather than zero.

Prototype Notes

Documents:

Component weights

Grade thresholds

Metric definitions

Minimum coverage logic

Prototype assumptions

Current data limitations

Unmatched NCRs

Contains supplier-linked NCR records that cannot currently be safely matched to the PO Vendor + Location scorecard.

Current count:

51

These are intentionally retained as exceptions rather than force-matched.

Vendor Review

Contains Vendor Master records requiring review due to:

missing required information, or

exact duplicate records.

Current count:

66

Key Prototype Design Decisions

The prototype intentionally follows several conservative data-quality principles:

Do not force fuzzy supplier matches into the scorecard.

Preserve unmatched NCRs as explicit exceptions.

Do not assume supplier-linked NCR means supplier-responsible NCR.

Do not score Lead-Time when benchmark coverage is insufficient.

Do not treat unavailable component scores as zero.

Do not combine raw commercial values across different currencies.

Keep scoring configuration separate from metric calculation logic.

Keep prototype rules visible and configurable for business review.

Items Requiring Business Review

Before production use, the following should be reviewed with IT / Supply Chain stakeholders:

Confirm final scorecard weights.

Confirm A/B/C/D thresholds.

Confirm minimum sample sizes.

Confirm the official On-Time Delivery target-date rule.

Confirm whether partial receipts should affect OTD.

Identify a reliable Lead-Time benchmark source.

Confirm how supplier responsibility is determined for NCRs.

Identify true supplier-response event timestamps for Responsiveness.

Confirm the preferred production Quality denominator.

Confirm the Commercial Performance methodology.

Confirm the preferred Vendor ID / CompanyID relationship from ETO.

Resolve or review the 51 unmatched supplier-linked NCRs.

Review the 66 Vendor Master quality exceptions.

Future Production Direction

The prototype is designed so that the Excel input layer can later be replaced by read-only ETO database access.

Potential future structure:

ETO Database
    |
    v
Reporting / Base Views
    |
    v
Vendor Scorecard Metric Logic
    |
    v
Scorecard Configuration
    |
    v
Historical Score Results
    |
    v
Power BI / Excel / Reporting

Potential database schemas discussed for future implementation include:

reporting
scorecard

The scoring logic should remain configurable rather than hard-coded into source queries.

Current Status

The current prototype includes:

Data Access Layer

Source validation

Vendor Master quality checks

Vendor + Location normalization

PO aggregation

NCR matching and exception handling

On-Time Delivery

Quality / NCR

Lead-Time logic

Responsiveness proxy

Commercial price stability

Component scoring

Prototype A/B/C/D grading

Overall vendor scoring

Individual Vendor Detail scorecard

Excel reporting and prototype notes

The next phase is stakeholder review and confirmation of the business rules before moving toward production database integration.