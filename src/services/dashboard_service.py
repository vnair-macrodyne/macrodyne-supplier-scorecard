from src.services.scorecard_service import (
    build_scorecard_data
)


def get_dashboard_summary():
    """
    Build the high-level metrics displayed on
    the Vendor Scorecard dashboard.
    """

    scorecard_data = (
        build_scorecard_data()
    )

    vendors = (
        scorecard_data[
            "vendors"
        ]
    )

    vendor_summary = (
        scorecard_data[
            "vendor_summary"
        ]
    )

    unmatched_ncrs = (
        scorecard_data[
            "unmatched_ncrs"
        ]
    )


    total_vendors = len(
        vendors
    )


    scored_vendors = int(
        vendor_summary[
            "prototype_overall_score"
        ]
        .notna()
        .sum()
    )


    vendor_review_count = int(
        vendors[
            "review_required"
        ]
        .sum()
    )


    unmatched_ncr_count = len(
        unmatched_ncrs
    )


    return {
        "total_vendors":
            total_vendors,

        "scored_vendors":
            scored_vendors,

        "vendor_review_count":
            vendor_review_count,

        "unmatched_ncr_count":
            unmatched_ncr_count,

        "source_description":
            scorecard_data[
                "source_description"
            ]
    }