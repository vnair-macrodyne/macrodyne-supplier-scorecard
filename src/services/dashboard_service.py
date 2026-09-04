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


    # ==================================================
    # HIGH-LEVEL COUNTS
    # ==================================================

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


    # ==================================================
    # OVERALL GRADE DISTRIBUTION
    # ==================================================

    grade_counts = (
        vendor_summary[
            "prototype_overall_grade"
        ]
        .value_counts()
    )


    grade_distribution = {
        "A": int(
            grade_counts.get(
                "A",
                0
            )
        ),

        "B": int(
            grade_counts.get(
                "B",
                0
            )
        ),

        "C": int(
            grade_counts.get(
                "C",
                0
            )
        ),

        "D": int(
            grade_counts.get(
                "D",
                0
            )
        ),
    }
    # ==================================================
    # COMPONENT SCORING COVERAGE
    # ==================================================

    total_scorecard_rows = len(
        vendor_summary
    )


    component_score_columns = {
        "On-Time Delivery":
            "delivery_prototype_score",

        "Quality / NCR":
            "quality_prototype_score",

        "Lead-Time Performance":
            "lead_time_prototype_score",

        "Responsiveness Proxy":
            "responsiveness_prototype_score",

        "Commercial Performance":
            "commercial_prototype_score",
    }


    component_coverage = []


    for (
        component_name,
        score_column
    ) in component_score_columns.items():

        scored_count = int(
            vendor_summary[
                score_column
            ]
            .notna()
            .sum()
        )

        not_scored_count = (
            total_scorecard_rows
            - scored_count
        )

        coverage_pct = (
            scored_count
            / total_scorecard_rows
            * 100
            if total_scorecard_rows
            else 0
        )


        component_coverage.append(
            {
                "component":
                    component_name,

                "scored":
                    scored_count,

                "not_scored":
                    not_scored_count,

                "coverage_pct":
                    round(
                        coverage_pct,
                        1
                    ),
            }
        )

    # ==================================================
    # RETURN DASHBOARD DATA
    # ==================================================

    return {
        "total_vendors":
            total_vendors,

        "scored_vendors":
            scored_vendors,

        "vendor_review_count":
            vendor_review_count,

        "unmatched_ncr_count":
            unmatched_ncr_count,

        "grade_distribution":
            grade_distribution,

        "total_scorecard_rows":
            total_scorecard_rows,

        "component_coverage":
            component_coverage,

        "source_description":
            scorecard_data[
                "source_description"
            ]
    }