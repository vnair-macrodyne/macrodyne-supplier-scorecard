import pandas as pd

from datetime import datetime
from functools import lru_cache


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


@lru_cache(maxsize=1)
def build_scorecard_data():

    """
    Run the Vendor Scorecard calculation pipeline.

    This function contains the reusable business-processing
    orchestration shared by the console/Excel workflow and
    the Flask web application.

    It intentionally does not:
        - print console diagnostics
        - create Excel files
        - contain Flask/UI logic

    Returns the processed datasets needed by downstream
    reporting or web interfaces.
    """

    # ==================================================
    # REPOSITORY / CONFIGURATION
    # ==================================================

    repo = create_repository()
    refreshed_at = datetime.now().astimezone()

    scorecard_rules = load_scorecard_rules(
        "config/scorecard_rules.json"
    )


    # ==================================================
    # SOURCE DATA
    # ==================================================

    items = repo.get_items()

    purchase = repo.get_purchase_orders()

    ncr_data = repo.get_ncrs()

    vendors = repo.get_vendors()


    # ==================================================
    # VENDOR MASTER QUALITY
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


    # ==================================================
    # PURCHASE ORDER PREPARATION
    # ==================================================

    purchase = prepare_purchase_order_vendors(
        purchase
    )


    # ==================================================
    # DELIVERY
    # ==================================================

    purchase = prepare_delivery_metrics(
        purchase
    )


    # ==================================================
    # LEAD TIME
    # ==================================================

    purchase = prepare_lead_time_metrics(
        purchase,
        items
    )


    # ==================================================
    # COMMERCIAL
    # ==================================================

    purchase = prepare_commercial_metrics(
        purchase
    )


    # ==================================================
    # NCR PREPARATION
    # ==================================================

    ncr_data = prepare_ncr_metrics(
        ncr_data
    )


    # ==================================================
    # PO VENDOR AGGREGATION
    # ==================================================

    vendor_summary = (
        aggregate_purchase_orders_by_vendor(
            purchase
        )
    )


    # ==================================================
    # NCR → PO VENDOR MATCHING
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


    # ==================================================
    # UNMATCHED NCR EXCEPTIONS
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


    # ==================================================
    # MATCHED NCR AGGREGATION
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


    # ==================================================
    # NCR → VENDOR SCORECARD MERGE
    # ==================================================

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


    # ==================================================
    # HANDLE VENDORS WITHOUT MATCHED NCRs
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


    # ==================================================
    # PROTOTYPE SCORING
    # ==================================================

    vendor_summary = apply_vendor_scoring(
        vendor_summary,
        scorecard_rules
    )


    # ==================================================
    # RETURN REUSABLE RESULT
    # ==================================================

    return {
        "source_description":
            describe(repo),

        "refreshed_at":
            refreshed_at,

        "scorecard_rules":
            scorecard_rules,

        "items":
            items,

        "purchase":
            purchase,

        "ncr_data":
            ncr_data,

        "vendors":
            vendors,

        "vendor_summary":
            vendor_summary,

        "unmatched_ncrs":
            fallback_check,

        "matched_supplier_ncrs":
            matched_supplier_ncrs
    }

def refresh_scorecard_data():
    """
    Clear the cached scorecard and rebuild it
    from the current source data.
    """

    build_scorecard_data.cache_clear()

    return build_scorecard_data()

def get_fresh_scorecard_data(
    max_age_minutes=15
):
    """
    Return cached scorecard data when it is still fresh.

    If the cached data is older than max_age_minutes,
    clear the cache and rebuild the scorecard from the
    current source data.
    """

    scorecard_data = (
        build_scorecard_data()
    )

    refreshed_at = (
        scorecard_data[
            "refreshed_at"
        ]
    )

    now = datetime.now().astimezone()

    age_seconds = (
        now - refreshed_at
    ).total_seconds()

    max_age_seconds = (
        max_age_minutes * 60
    )


    if age_seconds >= max_age_seconds:

        return refresh_scorecard_data()


    return scorecard_data