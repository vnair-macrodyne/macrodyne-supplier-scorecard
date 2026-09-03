import pandas as pd

from src.data_access.repository_factory import (
    create_repository,
    describe
)

from src.quality.vendor_quality import (
    classify_vendor_completeness,
    identify_exact_duplicates,
    assign_vendor_review_status
)

from src.matching.vendor_matcher import (
    prepare_purchase_order_vendors
)

from src.evaluation.delivery_evaluator import (
    prepare_delivery_metrics
)

from src.evaluation.lead_time_evaluator import (
    prepare_lead_time_metrics
)

from src.evaluation.commercial_evaluator import (
    prepare_commercial_metrics
)

from src.evaluation.ncr_evaluator import (
    prepare_ncr_metrics
)

from src.aggregation.vendor_aggregator import (
    aggregate_purchase_orders_by_vendor
)

from src.aggregation.ncr_aggregator import (
    aggregate_ncrs_by_vendor
)

from src.scoring.vendor_scoring import (
    load_scorecard_rules,
    apply_vendor_scoring
)

from src.reporting.excel_exporter import (
    export_vendor_scorecard
)


# ==================================================
# REPOSITORY SETUP
# ==================================================

# Source selection. Defaults to Excel with exactly the arguments this line
# carried before, so an unchanged environment behaves as it always did.
#
#   python main.py                       Excel (default)
#   python main.py --source=eto          read from ETO
#   SCORECARD_SOURCE=eto python main.py  same, via the environment

repo = create_repository()

print(describe(repo))

has_errors = False


# ==================================================
# LOAD PROTOTYPE SCORECARD RULES
# ==================================================

scorecard_rules = load_scorecard_rules(
    "config/scorecard_rules.json"
)


print("\nPrototype Scorecard Configuration")
print("---------------------------------")

print(
    f"Prototype mode: "
    f"{scorecard_rules['prototype']}"
)

print("\nConfigured component weights:")

for (
    component_name,
    component_config
) in scorecard_rules["components"].items():

    print(
        f"{component_config['label']}: "
        f"{component_config['weight']}%"
    )


# ==================================================
# DATA ACCESS / VALIDATION
# ==================================================

try:
    items = repo.get_items()

    print(
        f"\nItems PASS "
        f"{items.shape[0]} rows"
    )

except ValueError as error:

    print(
        f"Items FAILED: "
        f"{error}"
    )

    has_errors = True


try:
    purchase = repo.get_purchase_orders()

    print(
        f"Purchase Orders PASS "
        f"{purchase.shape[0]} rows"
    )

except ValueError as error:

    print(
        f"Purchase Orders FAILED: "
        f"{error}"
    )

    has_errors = True


try:
    ncr_data = repo.get_ncrs()

    print(
        f"NCRs PASS "
        f"{ncr_data.shape[0]} rows"
    )

except ValueError as error:

    print(
        f"NCRs FAILED: "
        f"{error}"
    )

    has_errors = True


try:
    vendors = repo.get_vendors()

    print(
        f"Vendors PASS "
        f"{vendors.shape[0]} rows"
    )

except ValueError as error:

    print(
        f"Vendors FAILED: "
        f"{error}"
    )

    has_errors = True


# ==================================================
# PROCESSING GATE
# ==================================================

if has_errors:

    print(
        "\nValidation errors detected."
        "\nScorecard processing stopped."
    )

else:

    print(
        "\nAll required datasets validated successfully."
        "\nReady for scorecard processing."
    )


    # ==================================================
    # STAGE 1 - VENDOR MASTER QUALITY
    # ==================================================

    vendors = classify_vendor_completeness(
        vendors
    )

    vendors = identify_exact_duplicates(
        vendors
    )

    vendors = assign_vendor_review_status(
        vendors
    )


    print("\nVendor Master Quality")
    print("---------------------")

    print(
        vendors[
            "vendor_quality_status"
        ]
        .value_counts()
    )

    print(
        f"\nTotal vendor records: "
        f"{vendors.shape[0]}"
    )


    incomplete_vendors = vendors[
        vendors[
            "vendor_quality_status"
        ] == "INCOMPLETE"
    ].copy()


    print("\nIncomplete Vendor Field Breakdown")
    print("---------------------------------")

    print(
        incomplete_vendors[
            [
                "vendor_name",
                "address_line_1",
                "postal_code"
            ]
        ]
        .isna()
        .sum()
    )


    exact_duplicates = vendors[
        vendors[
            "exact_duplicate_flag"
        ]
    ].copy()


    exact_duplicate_groups = (
        exact_duplicates[
            [
                "vendor_name_key",
                "address_key",
                "postal_code_key"
            ]
        ]
        .drop_duplicates()
        .shape[0]
    )


    print(
        f"\nExact duplicate vendor records: "
        f"{exact_duplicates.shape[0]}"
    )

    print(
        f"Exact duplicate groups: "
        f"{exact_duplicate_groups}"
    )


    print("\nVendor Review Summary")
    print("---------------------")

    print(
        vendors[
            "review_reason"
        ]
        .value_counts()
    )

    print(
        f"\nVendors requiring review: "
        f"{vendors['review_required'].sum()}"
    )


    # ==================================================
    # STAGE 2 - PO VENDOR PREPARATION
    # ==================================================

    purchase = prepare_purchase_order_vendors(
        purchase
    )


    print("\nPurchase Order Vendor Preparation")
    print("---------------------------------")

    print(
        purchase[
            [
                "vendor_name",
                "vendor_match_name",
                "vendor_match_city"
            ]
        ]
        .head()
    )

    print(
        f"\nPrepared PO transactions: "
        f"{purchase.shape[0]}"
    )


    # ==================================================
    # STAGE 3 - DELIVERY
    # ==================================================

    purchase = prepare_delivery_metrics(
        purchase
    )


    delivery_eligible_count = (
        purchase[
            "delivery_eligible"
        ]
        .sum()
    )

    delivery_ineligible_count = (
        purchase.shape[0]
        - delivery_eligible_count
    )

    on_time_count = (
        purchase[
            "on_time"
        ]
        .sum()
    )

    late_count = (
        purchase[
            "late"
        ]
        .sum()
    )


    print("\nDelivery Eligibility")
    print("--------------------")

    print(
        f"Delivery eligible PO rows: "
        f"{delivery_eligible_count}"
    )

    print(
        f"Delivery ineligible PO rows: "
        f"{delivery_ineligible_count}"
    )

    print(
        f"Total PO rows: "
        f"{purchase.shape[0]}"
    )


    print("\nDelivery Classification")
    print("-----------------------")

    print(
        f"On-time eligible PO rows: "
        f"{on_time_count}"
    )

    print(
        f"Late eligible PO rows: "
        f"{late_count}"
    )

    print(
        f"Eligible rows represented: "
        f"{on_time_count + late_count}"
    )


    # ==================================================
    # STAGE 4 - LEAD-TIME DIAGNOSTIC
    # ==================================================

    print("\nItem Master Lead-Time Diagnostic")
    print("--------------------------------")

    print(
        f"Total Item Master rows: "
        f"{items.shape[0]}"
    )

    print(
        f"Item Master rows with Lead Time populated: "
        f"{items['lead_time'].notna().sum()}"
    )

    print(
        f"Item Master unique parts with Lead Time populated: "
        f"{items.loc[items['lead_time'].notna(), 'part_number'].nunique()}"
    )


    # ==================================================
    # STAGE 5 - LEAD-TIME
    # ==================================================

    po_rows_before_lead_time = (
        purchase.shape[0]
    )


    purchase = prepare_lead_time_metrics(
        purchase,
        items
    )


    po_rows_after_lead_time = (
        purchase.shape[0]
    )


    item_part_keys = set(
        items[
            "part_number"
        ]
        .dropna()
        .astype(str)
        .str.strip()
        .str.upper()
    )


    po_part_match_count = (
        purchase[
            "part_number"
        ]
        .dropna()
        .astype(str)
        .str.strip()
        .str.upper()
        .isin(
            item_part_keys
        )
        .sum()
    )


    po_rows_with_item_lead_time = (
        purchase[
            "item_lead_time_days"
        ]
        .notna()
        .sum()
    )


    lead_time_eligible_count = (
        purchase[
            "lead_time_eligible"
        ]
        .sum()
    )

    lead_time_adherent_count = (
        purchase[
            "lead_time_adherent"
        ]
        .sum()
    )

    lead_time_non_adherent_count = (
        lead_time_eligible_count
        - lead_time_adherent_count
    )


    print("\nLead-Time Part Match Diagnostic")
    print("-------------------------------")

    print(
        f"PO rows whose Part Number exists in Item Master: "
        f"{po_part_match_count}"
    )

    print(
        f"PO rows receiving usable Item Master Lead Time: "
        f"{po_rows_with_item_lead_time}"
    )


    print("\nLead-Time Evaluation")
    print("--------------------")

    print(
        f"PO rows before Item Master merge: "
        f"{po_rows_before_lead_time}"
    )

    print(
        f"PO rows after Item Master merge: "
        f"{po_rows_after_lead_time}"
    )

    print(
        f"Lead-time eligible PO rows: "
        f"{lead_time_eligible_count}"
    )

    print(
        f"Lead-time adherent PO rows: "
        f"{lead_time_adherent_count}"
    )

    print(
        f"Lead-time non-adherent PO rows: "
        f"{lead_time_non_adherent_count}"
    )


    # ==================================================
    # STAGE 6 - COMMERCIAL
    # ==================================================

    po_rows_before_commercial = (
        purchase.shape[0]
    )


    purchase = prepare_commercial_metrics(
        purchase
    )


    po_rows_after_commercial = (
        purchase.shape[0]
    )


    commercial_base_eligible_count = (
        purchase[
            "commercial_base_eligible"
        ]
        .sum()
    )

    price_comparison_count = (
        purchase[
            "price_comparison_eligible"
        ]
        .sum()
    )

    price_stable_count = (
        purchase[
            "price_stable"
        ]
        .sum()
    )

    price_increase_count = (
        purchase[
            "price_increased"
        ]
        .sum()
    )


    print("\nCommercial Performance Preparation")
    print("----------------------------------")

    print(
        f"PO rows before commercial preparation: "
        f"{po_rows_before_commercial}"
    )

    print(
        f"PO rows after commercial preparation: "
        f"{po_rows_after_commercial}"
    )

    print(
        f"Commercial base-eligible PO rows: "
        f"{commercial_base_eligible_count}"
    )

    print(
        f"Repeat price comparisons available: "
        f"{price_comparison_count}"
    )

    print(
        f"Price-stable comparisons: "
        f"{price_stable_count}"
    )

    print(
        f"Price-increase comparisons: "
        f"{price_increase_count}"
    )

    print(
        f"Price comparisons represented: "
        f"{price_stable_count + price_increase_count}"
    )


    # ==================================================
    # STAGE 7 - NCR PREPARATION
    # ==================================================

    ncr_data = prepare_ncr_metrics(
        ncr_data
    )


    supplier_linked_count = (
        ncr_data[
            "supplier_linked"
        ]
        .sum()
    )

    supplier_unlinked_count = (
        ncr_data.shape[0]
        - supplier_linked_count
    )

    supplier_linked_with_location = (
        ncr_data.loc[
            ncr_data[
                "supplier_linked"
            ],
            "vendor_match_city"
        ]
        .notna()
        .sum()
    )

    supplier_linked_without_location = (
        supplier_linked_count
        - supplier_linked_with_location
    )


    print("\nNCR Supplier Linkage")
    print("--------------------")

    print(
        f"Supplier-linked NCRs: "
        f"{supplier_linked_count}"
    )

    print(
        f"NCRs without supplier: "
        f"{supplier_unlinked_count}"
    )

    print(
        f"Total NCRs: "
        f"{ncr_data.shape[0]}"
    )

    print(
        f"Supplier-linked NCRs with location: "
        f"{supplier_linked_with_location}"
    )

    print(
        f"Supplier-linked NCRs without location: "
        f"{supplier_linked_without_location}"
    )


    # ==================================================
    # QUALITY DIAGNOSTIC
    # ==================================================

    supplier_quality_eligible_count = (
        ncr_data.loc[
            ncr_data[
                "supplier_linked"
            ],
            "quality_eligible"
        ]
        .sum()
    )

    supplier_quantity_anomaly_count = (
        ncr_data.loc[
            ncr_data[
                "supplier_linked"
            ],
            "ncr_quantity_anomaly"
        ]
        .sum()
    )


    print("\nNCR Quality Preparation")
    print("-----------------------")

    print(
        f"Quality-eligible supplier NCRs: "
        f"{supplier_quality_eligible_count}"
    )

    print(
        f"Supplier NCR quantity anomalies: "
        f"{supplier_quantity_anomaly_count}"
    )


    # ==================================================
    # RESPONSIVENESS DIAGNOSTIC
    # ==================================================

    responsiveness_eligible_count = (
        ncr_data.loc[
            ncr_data[
                "supplier_linked"
            ],
            "responsiveness_eligible"
        ]
        .sum()
    )

    resolved_supplier_ncr_count = (
        ncr_data.loc[
            ncr_data[
                "supplier_linked"
            ],
            "resolved_flag"
        ]
        .sum()
    )

    unresolved_supplier_ncr_count = (
        ncr_data.loc[
            ncr_data[
                "supplier_linked"
            ],
            "unresolved_flag"
        ]
        .sum()
    )


    print("\nResponsiveness Proxy Preparation")
    print("--------------------------------")

    print(
        f"Responsiveness-eligible supplier NCRs: "
        f"{responsiveness_eligible_count}"
    )

    print(
        f"Resolved supplier NCRs: "
        f"{resolved_supplier_ncr_count}"
    )

    print(
        f"Unresolved supplier NCRs: "
        f"{unresolved_supplier_ncr_count}"
    )


    # ==================================================
    # STAGE 8 - PO VENDOR AGGREGATION
    # ==================================================

    vendor_summary = (
        aggregate_purchase_orders_by_vendor(
            purchase
        )
    )


    print("\nVendor Aggregation Controls")
    print("---------------------------")

    print(
        f"Aggregated vendor rows: "
        f"{vendor_summary.shape[0]}"
    )

    print(
        f"PO transactions represented: "
        f"{vendor_summary['po_transaction_count'].sum()}"
    )

    print(
        f"Delivery eligible represented: "
        f"{vendor_summary['delivery_eligible_count'].sum()}"
    )

    print(
        f"Lead-time eligible represented: "
        f"{vendor_summary['lead_time_eligible_count'].sum()}"
    )

    print(
        f"Price comparisons represented: "
        f"{vendor_summary['price_comparison_count'].sum()}"
    )


    # ==================================================
    # STAGE 9 - NCR TO PO VENDOR MATCH
    # ==================================================

    po_vendor_keys = (
        vendor_summary.loc[
            vendor_summary[
                "vendor_match_name"
            ].notna()
            &
            vendor_summary[
                "vendor_match_city"
            ].notna(),
            [
                "vendor_match_name",
                "vendor_match_city"
            ]
        ]
        .drop_duplicates()
        .copy()
    )


    supplier_ncrs = (
        ncr_data[
            ncr_data[
                "supplier_linked"
            ]
        ]
        .copy()
    )


    location_ready_ncrs = (
        supplier_ncrs.loc[
            supplier_ncrs[
                "vendor_match_name"
            ].notna()
            &
            supplier_ncrs[
                "vendor_match_city"
            ].notna()
        ]
        .copy()
    )


    location_missing_ncrs = (
        supplier_ncrs.loc[
            supplier_ncrs[
                "vendor_match_city"
            ].isna()
        ]
        .copy()
    )


    ncr_match_check = (
        location_ready_ncrs.merge(
            po_vendor_keys,
            on=[
                "vendor_match_name",
                "vendor_match_city"
            ],
            how="left",
            indicator=True
        )
    )


    matched_ncr_count = (
        ncr_match_check[
            "_merge"
        ]
        .eq("both")
        .sum()
    )

    unmatched_location_ready_count = (
        ncr_match_check[
            "_merge"
        ]
        .eq("left_only")
        .sum()
    )

    missing_location_count = (
        location_missing_ncrs.shape[0]
    )


    print("\nNCR to PO Vendor Match")
    print("----------------------")

    print(
        f"Supplier-linked NCRs matched by Name + City: "
        f"{matched_ncr_count}"
    )

    print(
        f"Supplier-linked NCRs with Name + City but not matched: "
        f"{unmatched_location_ready_count}"
    )

    print(
        f"Supplier-linked NCRs missing location: "
        f"{missing_location_count}"
    )

    print(
        f"Total supplier-linked NCRs accounted for: "
        f"{matched_ncr_count + unmatched_location_ready_count + missing_location_count}"
    )


    # ==================================================
    # STAGE 10 - NCR FALLBACK DIAGNOSTIC
    # ==================================================

    po_name_locations = (
        vendor_summary[
            [
                "vendor_match_name",
                "vendor_match_city"
            ]
        ]
        .drop_duplicates()
        .groupby(
            "vendor_match_name",
            dropna=False
        )
        .agg(
            po_location_count=(
                "vendor_match_city",
                lambda locations: (
                    locations
                    .dropna()
                    .nunique()
                )
            )
        )
        .reset_index()
    )


    unmatched_location_ready_ncrs = (
        ncr_match_check.loc[
            ncr_match_check[
                "_merge"
            ] == "left_only"
        ]
        .drop(
            columns=[
                "_merge"
            ]
        )
        .copy()
    )


    fallback_ncrs = (
        pd.concat(
            [
                unmatched_location_ready_ncrs,
                location_missing_ncrs
            ],
            ignore_index=True
        )
    )


    fallback_check = (
        fallback_ncrs.merge(
            po_name_locations,
            on="vendor_match_name",
            how="left"
        )
    )


    print("\nNCR Name-Only Fallback Diagnostic")
    print("---------------------------------")

    print(
        f"Unmatched NCRs retained: "
        f"{fallback_check.shape[0]}"
    )


    # ==================================================
    # STAGE 11 - MATCHED NCR AGGREGATION
    # ==================================================

    matched_supplier_ncrs = (
        ncr_match_check.loc[
            ncr_match_check[
                "_merge"
            ] == "both"
        ]
        .drop(
            columns=[
                "_merge"
            ]
        )
        .copy()
    )


    ncr_summary = (
        aggregate_ncrs_by_vendor(
            matched_supplier_ncrs
        )
    )


    print("\nNCR Aggregation Controls")
    print("------------------------")

    print(
        f"Matched NCR rows aggregated: "
        f"{ncr_summary['supplier_linked_ncr_count'].sum()}"
    )

    print(
        f"Quality-eligible NCRs represented: "
        f"{ncr_summary['quality_eligible_ncr_count'].sum()}"
    )

    print(
        f"NCR quantity anomalies represented: "
        f"{ncr_summary['ncr_quantity_anomaly_count'].sum()}"
    )

    print(
        f"Responsiveness-eligible NCRs represented: "
        f"{ncr_summary['responsiveness_eligible_ncr_count'].sum()}"
    )

    print(
        f"Resolved NCRs represented: "
        f"{ncr_summary['resolved_ncr_count'].sum()}"
    )

    print(
        f"Unresolved NCRs represented: "
        f"{ncr_summary['unresolved_ncr_count'].sum()}"
    )


    # ==================================================
    # STAGE 12 - NCR MERGE
    # ==================================================

    vendor_rows_before_ncr_merge = (
        vendor_summary.shape[0]
    )


    vendor_summary = (
        vendor_summary.merge(
            ncr_summary,
            on=[
                "vendor_match_name",
                "vendor_match_city"
            ],
            how="left"
        )
    )


    vendor_rows_after_ncr_merge = (
        vendor_summary.shape[0]
    )


    # ==================================================
    # HANDLE VENDORS WITHOUT MATCHED NCR
    # ==================================================

    no_ncr_mask = (
        vendor_summary[
            "supplier_linked_ncr_count"
        ]
        .isna()
    )


    vendor_summary.loc[
        no_ncr_mask,
        "total_rejected_qty"
    ] = 0

    vendor_summary.loc[
        no_ncr_mask,
        "total_ncr_quantity"
    ] = 0

    vendor_summary.loc[
        no_ncr_mask,
        "quality_rejected_qty"
    ] = 0


    count_columns = [
        "supplier_linked_ncr_count",
        "quality_eligible_ncr_count",
        "ncr_quantity_anomaly_count",
        "responsiveness_eligible_ncr_count",
        "resolved_ncr_count",
        "unresolved_ncr_count"
    ]


    for column in count_columns:

        vendor_summary[
            column
        ] = (
            vendor_summary[
                column
            ]
            .fillna(0)
            .astype(int)
        )


    print("\nNCR Scorecard Merge Controls")
    print("----------------------------")

    print(
        f"Vendor rows before NCR merge: "
        f"{vendor_rows_before_ncr_merge}"
    )

    print(
        f"Vendor rows after NCR merge: "
        f"{vendor_rows_after_ncr_merge}"
    )

    print(
        f"Matched NCRs represented: "
        f"{vendor_summary['supplier_linked_ncr_count'].sum()}"
    )

    print(
        f"Unmatched NCRs retained outside scorecard: "
        f"{fallback_check.shape[0]}"
    )


    # ==================================================
    # STAGE 13 - PROTOTYPE VENDOR SCORING
    # ==================================================

    vendor_summary = apply_vendor_scoring(
        vendor_summary,
        scorecard_rules
    )


    # ==================================================
    # COMPONENT SCORING COVERAGE
    # ==================================================

    print("\nPrototype Component Scoring Coverage")
    print("------------------------------------")


    component_score_columns = {

        "Delivery":
            "delivery_prototype_score",

        "Quality":
            "quality_prototype_score",

        "Lead-Time":
            "lead_time_prototype_score",

        "Responsiveness":
            "responsiveness_prototype_score",

        "Commercial":
            "commercial_prototype_score"
    }


    for (
        component_name,
        score_column
    ) in component_score_columns.items():

        scored_count = (
            vendor_summary[
                score_column
            ]
            .notna()
            .sum()
        )

        not_scored_count = (
            vendor_summary.shape[0]
            - scored_count
        )

        print(
            f"{component_name}: "
            f"{scored_count} scored / "
            f"{not_scored_count} N/A"
        )


    # ==================================================
    # COMPONENT GRADE DISTRIBUTIONS
    # ==================================================

    print("\nPrototype Component Grade Distributions")
    print("---------------------------------------")


    component_grade_columns = {

        "Delivery":
            "delivery_prototype_grade",

        "Quality":
            "quality_prototype_grade",

        "Lead-Time":
            "lead_time_prototype_grade",

        "Responsiveness":
            "responsiveness_prototype_grade",

        "Commercial":
            "commercial_prototype_grade"
    }


    for (
        component_name,
        grade_column
    ) in component_grade_columns.items():

        print(
            f"\n{component_name}"
        )

        print(
            vendor_summary[
                grade_column
            ]
            .value_counts(
                dropna=False
            )
        )


    # ==================================================
    # NEW - QUALITY SCORING RULE DIAGNOSTIC
    # ==================================================
    #
    # This tells us how many Quality scores are based on
    # actual NCR calculations versus the prototype rule:
    #
    # "No supplier-linked NCRs + sufficient PO activity"
    #
    # ==================================================

    print("\nQuality Score Status Distribution")
    print("---------------------------------")

    print(
        vendor_summary[
            "quality_score_status"
        ]
        .value_counts(
            dropna=False
        )
    )


    print("\nQuality Grade by Score Status")
    print("-----------------------------")

    print(
        pd.crosstab(
            vendor_summary[
                "quality_score_status"
            ],
            vendor_summary[
                "quality_prototype_grade"
            ],
            margins=True
        )
    )


    # ==================================================
    # OVERALL SCORE COVERAGE
    # ==================================================

    overall_scored_count = (
        vendor_summary[
            "prototype_overall_score"
        ]
        .notna()
        .sum()
    )

    overall_not_scored_count = (
        vendor_summary.shape[0]
        - overall_scored_count
    )


    print("\nPrototype Overall Score Coverage")
    print("--------------------------------")

    print(
        f"Vendors receiving Prototype Overall Score: "
        f"{overall_scored_count}"
    )

    print(
        f"Vendors without sufficient component coverage: "
        f"{overall_not_scored_count}"
    )

    print(
        f"Total vendor/location rows: "
        f"{vendor_summary.shape[0]}"
    )


    # ==================================================
    # OVERALL GRADE DISTRIBUTION
    # ==================================================

    print("\nPrototype Overall Grade Distribution")
    print("------------------------------------")

    print(
        vendor_summary[
            "prototype_overall_grade"
        ]
        .value_counts(
            dropna=False
        )
    )


    # ==================================================
    # SCORE COVERAGE DISTRIBUTION
    # ==================================================

    print("\nScored Component Count Distribution")
    print("-----------------------------------")

    print(
        vendor_summary[
            "prototype_scored_component_count"
        ]
        .value_counts()
        .sort_index()
    )


    print("\nPrototype Weight Coverage Distribution")
    print("--------------------------------------")

    print(
        vendor_summary[
            "prototype_weight_coverage_pct"
        ]
        .round(2)
        .value_counts()
        .sort_index()
    )


    # ==================================================
    # COMPLETE SCORECARD PREVIEW
    # ==================================================

    print("\nComplete Vendor Scorecard Preview")
    print("---------------------------------")

    print(
        vendor_summary[
            [
                "vendor_match_name",
                "vendor_match_city",

                # --------------------------------------
                # Activity
                # --------------------------------------
                "po_transaction_count",

                # --------------------------------------
                # Delivery
                # --------------------------------------
                "delivery_eligible_count",
                "on_time_delivery_pct",
                "delivery_prototype_score",
                "delivery_prototype_grade",
                "delivery_score_status",

                # --------------------------------------
                # Quality
                # --------------------------------------
                "supplier_linked_ncr_count",
                "quality_eligible_ncr_count",
                "ncr_rejected_pct",
                "quality_prototype_score",
                "quality_prototype_grade",
                "quality_score_status",

                # --------------------------------------
                # Lead-Time
                # --------------------------------------
                "lead_time_eligible_count",
                "lead_time_adherence_pct",
                "lead_time_prototype_score",
                "lead_time_prototype_grade",
                "lead_time_score_status",

                # --------------------------------------
                # Responsiveness
                # --------------------------------------
                "responsiveness_eligible_ncr_count",
                "responsiveness_proxy_pct",
                "responsiveness_prototype_score",
                "responsiveness_prototype_grade",
                "responsiveness_score_status",

                # --------------------------------------
                # Commercial
                # --------------------------------------
                "price_comparison_count",
                "price_stability_pct",
                "commercial_prototype_score",
                "commercial_prototype_grade",
                "commercial_score_status",

                # --------------------------------------
                # Overall
                # --------------------------------------
                "prototype_scored_component_count",
                "prototype_weight_coverage_pct",
                "prototype_overall_score",
                "prototype_overall_grade",
                "prototype_overall_status"
            ]
        ]
        .head(20)
    )


    # ==================================================
    # EXAMPLE INDIVIDUAL VENDOR SCORECARD
    # ==================================================

    scored_vendor_examples = (
        vendor_summary[
            vendor_summary[
                "prototype_overall_score"
            ].notna()
        ]
        .sort_values(
            "prototype_overall_score",
            ascending=False
        )
    )


    if not scored_vendor_examples.empty:

        example_vendor = (
            scored_vendor_examples.iloc[0]
        )


        print("\nExample Individual Vendor Scorecard")
        print("-----------------------------------")

        print(
            f"Vendor: "
            f"{example_vendor['vendor_match_name']}"
        )

        print(
            f"Location: "
            f"{example_vendor['vendor_match_city']}"
        )

        print(
            f"PO Transactions: "
            f"{example_vendor['po_transaction_count']}"
        )


        # ==================================================
        # DELIVERY
        # ==================================================

        print("\nOn-Time Delivery")

        print(
            f"Eligible Transactions: "
            f"{example_vendor['delivery_eligible_count']}"
        )

        print(
            f"Metric: "
            f"{example_vendor['on_time_delivery_pct']}"
        )

        print(
            f"Prototype Score: "
            f"{example_vendor['delivery_prototype_score']}"
        )

        print(
            f"Prototype Grade: "
            f"{example_vendor['delivery_prototype_grade']}"
        )

        print(
            f"Status: "
            f"{example_vendor['delivery_score_status']}"
        )


        # ==================================================
        # QUALITY
        # ==================================================

        print("\nQuality")

        print(
            f"Supplier-Linked NCRs: "
            f"{example_vendor['supplier_linked_ncr_count']}"
        )

        print(
            f"Quality-Eligible NCRs: "
            f"{example_vendor['quality_eligible_ncr_count']}"
        )

        print(
            f"NCR Rejected %: "
            f"{example_vendor['ncr_rejected_pct']}"
        )

        print(
            f"Prototype Score: "
            f"{example_vendor['quality_prototype_score']}"
        )

        print(
            f"Prototype Grade: "
            f"{example_vendor['quality_prototype_grade']}"
        )

        print(
            f"Status: "
            f"{example_vendor['quality_score_status']}"
        )


        # ==================================================
        # LEAD-TIME
        # ==================================================

        print("\nLead-Time")

        print(
            f"Eligible Transactions: "
            f"{example_vendor['lead_time_eligible_count']}"
        )

        print(
            f"Metric: "
            f"{example_vendor['lead_time_adherence_pct']}"
        )

        print(
            f"Prototype Score: "
            f"{example_vendor['lead_time_prototype_score']}"
        )

        print(
            f"Prototype Grade: "
            f"{example_vendor['lead_time_prototype_grade']}"
        )

        print(
            f"Status: "
            f"{example_vendor['lead_time_score_status']}"
        )


        # ==================================================
        # RESPONSIVENESS
        # ==================================================

        print("\nResponsiveness Proxy")

        print(
            f"Eligible NCRs: "
            f"{example_vendor['responsiveness_eligible_ncr_count']}"
        )

        print(
            f"Metric: "
            f"{example_vendor['responsiveness_proxy_pct']}"
        )

        print(
            f"Prototype Score: "
            f"{example_vendor['responsiveness_prototype_score']}"
        )

        print(
            f"Prototype Grade: "
            f"{example_vendor['responsiveness_prototype_grade']}"
        )

        print(
            f"Status: "
            f"{example_vendor['responsiveness_score_status']}"
        )


        # ==================================================
        # COMMERCIAL
        # ==================================================

        print("\nCommercial")

        print(
            f"Repeat Price Comparisons: "
            f"{example_vendor['price_comparison_count']}"
        )

        print(
            f"Metric: "
            f"{example_vendor['price_stability_pct']}"
        )

        print(
            f"Prototype Score: "
            f"{example_vendor['commercial_prototype_score']}"
        )

        print(
            f"Prototype Grade: "
            f"{example_vendor['commercial_prototype_grade']}"
        )

        print(
            f"Status: "
            f"{example_vendor['commercial_score_status']}"
        )


        # ==================================================
        # OVERALL
        # ==================================================

        print("\nOverall")

        print(
            f"Scored Components: "
            f"{example_vendor['prototype_scored_component_count']}"
        )

        print(
            f"Weight Coverage %: "
            f"{example_vendor['prototype_weight_coverage_pct']}"
        )

        print(
            f"Prototype Overall Score: "
            f"{example_vendor['prototype_overall_score']}"
        )

        print(
            f"Prototype Overall Grade: "
            f"{example_vendor['prototype_overall_grade']}"
        )

        print(
            f"Status: "
            f"{example_vendor['prototype_overall_status']}"
        )


    # ==================================================
    # FINAL PROTOTYPE CONTROLS
    # ==================================================

    print("\nFinal Prototype Controls")
    print("------------------------")

    print(
        f"PO transactions represented: "
        f"{vendor_summary['po_transaction_count'].sum()}"
    )

    print(
        f"Vendor/location rows: "
        f"{vendor_summary.shape[0]}"
    )

    print(
        f"Delivery eligible: "
        f"{vendor_summary['delivery_eligible_count'].sum()}"
    )

    print(
        f"Lead-time eligible: "
        f"{vendor_summary['lead_time_eligible_count'].sum()}"
    )

    print(
        f"Commercial comparisons: "
        f"{vendor_summary['price_comparison_count'].sum()}"
    )

    print(
        f"Matched supplier-linked NCRs: "
        f"{vendor_summary['supplier_linked_ncr_count'].sum()}"
    )

    print(
        f"Unmatched supplier-linked NCRs: "
        f"{fallback_check.shape[0]}"
    )

    print(
        f"Total supplier-linked NCRs accounted for: "
        f"{vendor_summary['supplier_linked_ncr_count'].sum() + fallback_check.shape[0]}"
    )

    print(
        f"Prototype-scored vendors: "
        f"{overall_scored_count}"
    )


    # ==================================================
    # EXCEL OUTPUT
    # ==================================================

    output_path = export_vendor_scorecard(
        vendor_summary=vendor_summary,
        unmatched_ncrs=fallback_check,
        vendors=vendors
    )


    print("\nExcel Prototype Output")
    print("----------------------")

    print(
        f"Workbook created successfully: "
        f"{output_path}"
    )


    # ==================================================
    # PIPELINE STATUS
    # ==================================================

    print(
        "\nVendor Scorecard prototype "
        "processing completed successfully."
    )

    print(
        "\nFive metric components available:"
        "\n1. On-Time Delivery"
        "\n2. Quality / NCR"
        "\n3. Lead-Time Performance"
        "\n4. Responsiveness Proxy"
        "\n5. Commercial Performance"
    )

    print(
        "\nPrototype component scores, grades, "
        "and overall vendor grades are now calculated."
    )

    print(
        "\nIMPORTANT: Weights, thresholds, minimum samples, "
        "and grades are prototype assumptions and require "
        "business approval before production use."
    )

    print(
        "\nLead-Time remains unavailable for reliable "
        "scoring because Item Master benchmark coverage "
        "is currently insufficient."
    )