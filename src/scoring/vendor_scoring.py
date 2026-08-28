import json

import pandas as pd


# ==================================================
# LOAD SCORECARD RULES
# ==================================================

def load_scorecard_rules(
    config_path="config/scorecard_rules.json"
):
    """
    Load prototype scorecard rules from JSON.

    Weights, thresholds, and minimum sample requirements
    remain outside the calculation code so they can be
    changed later without rewriting metric logic.
    """

    with open(
        config_path,
        "r",
        encoding="utf-8"
    ) as config_file:

        rules = json.load(
            config_file
        )

    return rules


# ==================================================
# SAFE SCORE
# ==================================================

def _safe_score(value):
    """
    Constrain a component score to the range 0-100.

    Missing values remain missing.
    """

    if pd.isna(value):
        return float("nan")

    return max(
        0.0,
        min(
            100.0,
            float(value)
        )
    )


# ==================================================
# GRADE ASSIGNMENT
# ==================================================

def _assign_grade(
    score,
    grade_thresholds
):
    """
    Convert a numeric 0-100 score into the configured
    prototype A/B/C/D grade.
    """

    if pd.isna(score):
        return "N/A"

    ordered_grades = sorted(
        grade_thresholds.items(),
        key=lambda item: item[1],
        reverse=True
    )

    for (
        grade,
        minimum_score
    ) in ordered_grades:

        if score >= minimum_score:
            return grade

    return "N/A"


# ==================================================
# ON-TIME DELIVERY SCORE
# ==================================================

def _score_delivery(
    row,
    rules
):
    """
    Delivery Component

    Prototype Metric:
        On-Time Delivery %

    Higher is better.
    """

    component_rules = (
        rules["components"][
            "on_time_delivery"
        ]
    )

    minimum_sample = (
        component_rules[
            "minimum_sample"
        ]
    )

    eligible_count = (
        row[
            "delivery_eligible_count"
        ]
    )


    if eligible_count < minimum_sample:

        return (
            float("nan"),
            "INSUFFICIENT SAMPLE"
        )


    metric = (
        row[
            "on_time_delivery_pct"
        ]
    )


    if pd.isna(metric):

        return (
            float("nan"),
            "NO VALID DELIVERY METRIC"
        )


    return (
        _safe_score(metric),
        "SCORED"
    )


# ==================================================
# QUALITY SCORE
# ==================================================

def _score_quality(
    row,
    rules
):
    """
    Quality Component

    Prototype scoring metric:

        Supplier-Linked NCR Rate %
        =
        Supplier-Linked NCR Count
        -------------------------
        PO Transaction Count
        x 100

    Prototype Quality Score:

        100
        -
        (
            Supplier-Linked NCR Rate %
            x NCR Rate Penalty Factor
        )

    Example with penalty factor = 5:

        NCR Rate = 0%
        Score = 100

        NCR Rate = 2%
        Score = 90

        NCR Rate = 5%
        Score = 75

        NCR Rate = 10%
        Score = 50

    Lower NCR incidence is better.

    NCR Rejected % remains available separately as a
    descriptive severity indicator and is NOT directly
    used in the Quality Prototype Score.

    Important:
    Supplier-linked does not necessarily mean that the
    supplier has been formally confirmed responsible
    for the NCR.
    """

    component_rules = (
        rules["components"][
            "quality"
        ]
    )


    minimum_po_transactions = (
        component_rules[
            "minimum_po_transactions"
        ]
    )


    penalty_factor = (
        component_rules[
            "ncr_rate_penalty_factor"
        ]
    )


    po_count = (
        row[
            "po_transaction_count"
        ]
    )


    # ==================================================
    # MINIMUM PURCHASING ACTIVITY
    # ==================================================

    if (
        pd.isna(po_count)
        or po_count
        < minimum_po_transactions
    ):

        return (
            float("nan"),
            "INSUFFICIENT PO SAMPLE"
        )


    supplier_ncr_count = (
        row[
            "supplier_linked_ncr_count"
        ]
    )


    if pd.isna(
        supplier_ncr_count
    ):

        supplier_ncr_count = 0


    # ==================================================
    # NCR INCIDENCE RATE
    # ==================================================

    supplier_ncr_rate_pct = (
        supplier_ncr_count
        / po_count
        * 100
    )


    # ==================================================
    # APPLY PROTOTYPE NCR PENALTY
    # ==================================================

    quality_penalty = (
        supplier_ncr_rate_pct
        * penalty_factor
    )


    quality_score = (
        100
        - quality_penalty
    )


    return (
        _safe_score(
            quality_score
        ),
        "SCORED"
    )


# ==================================================
# LEAD-TIME SCORE
# ==================================================

def _score_lead_time(
    row,
    rules
):
    """
    Lead-Time Component

    Prototype Metric:
        Lead-Time Adherence %

    Higher is better.

    Current Item Master lead-time coverage is extremely
    limited, so this component will normally remain N/A.
    """

    component_rules = (
        rules["components"][
            "lead_time"
        ]
    )

    minimum_sample = (
        component_rules[
            "minimum_sample"
        ]
    )

    eligible_count = (
        row[
            "lead_time_eligible_count"
        ]
    )


    if eligible_count < minimum_sample:

        return (
            float("nan"),
            "INSUFFICIENT BENCHMARK DATA"
        )


    metric = (
        row[
            "lead_time_adherence_pct"
        ]
    )


    if pd.isna(metric):

        return (
            float("nan"),
            "NO VALID LEAD-TIME METRIC"
        )


    return (
        _safe_score(metric),
        "SCORED"
    )


# ==================================================
# RESPONSIVENESS SCORE
# ==================================================

def _score_responsiveness(
    row,
    rules
):
    """
    Responsiveness Component

    Prototype Proxy:
        NCR Resolution %

    Higher is better.

    This is not true supplier response-time performance.
    """

    component_rules = (
        rules["components"][
            "responsiveness"
        ]
    )

    minimum_sample = (
        component_rules[
            "minimum_sample"
        ]
    )

    eligible_count = (
        row[
            "responsiveness_eligible_ncr_count"
        ]
    )


    if eligible_count < minimum_sample:

        return (
            float("nan"),
            "INSUFFICIENT RESPONSE EVENTS"
        )


    metric = (
        row[
            "responsiveness_proxy_pct"
        ]
    )


    if pd.isna(metric):

        return (
            float("nan"),
            "NO VALID RESPONSIVENESS METRIC"
        )


    return (
        _safe_score(metric),
        "SCORED"
    )


# ==================================================
# COMMERCIAL SCORE
# ==================================================

def _score_commercial(
    row,
    rules
):
    """
    Commercial Component

    Prototype Metric:
        Price Stability %

    Higher is better.

    Price Stability means the current comparable unit
    price was equal to or below the previous comparable
    unit price.
    """

    component_rules = (
        rules["components"][
            "commercial"
        ]
    )

    minimum_sample = (
        component_rules[
            "minimum_sample"
        ]
    )

    comparison_count = (
        row[
            "price_comparison_count"
        ]
    )


    if comparison_count < minimum_sample:

        return (
            float("nan"),
            "INSUFFICIENT PRICE HISTORY"
        )


    metric = (
        row[
            "price_stability_pct"
        ]
    )


    if pd.isna(metric):

        return (
            float("nan"),
            "NO VALID COMMERCIAL METRIC"
        )


    return (
        _safe_score(metric),
        "SCORED"
    )


# ==================================================
# SCORE ALL VENDORS
# ==================================================

def apply_vendor_scoring(
    vendor_summary,
    rules
):
    """
    Apply prototype component scores, grades,
    coverage measures, and overall score to each
    Vendor + Location record.
    """

    vendor_summary = (
        vendor_summary.copy()
    )


    # ==================================================
    # QUALITY DESCRIPTIVE METRIC
    # ==================================================
    #
    # Supplier-Linked NCR Rate %
    #
    #       Supplier-Linked NCR Count
    #       -------------------------
    #          PO Transaction Count
    #
    # ==================================================

    vendor_summary[
        "supplier_linked_ncr_rate_pct"
    ] = float("nan")


    valid_po_mask = (
        vendor_summary[
            "po_transaction_count"
        ] > 0
    )


    vendor_summary.loc[
        valid_po_mask,
        "supplier_linked_ncr_rate_pct"
    ] = (
        vendor_summary.loc[
            valid_po_mask,
            "supplier_linked_ncr_count"
        ]
        /
        vendor_summary.loc[
            valid_po_mask,
            "po_transaction_count"
        ]
        * 100
    )


    # ==================================================
    # COMPONENT SCORING
    # ==================================================

    delivery_results = (
        vendor_summary.apply(
            lambda row: (
                _score_delivery(
                    row,
                    rules
                )
            ),
            axis=1
        )
    )


    quality_results = (
        vendor_summary.apply(
            lambda row: (
                _score_quality(
                    row,
                    rules
                )
            ),
            axis=1
        )
    )


    lead_time_results = (
        vendor_summary.apply(
            lambda row: (
                _score_lead_time(
                    row,
                    rules
                )
            ),
            axis=1
        )
    )


    responsiveness_results = (
        vendor_summary.apply(
            lambda row: (
                _score_responsiveness(
                    row,
                    rules
                )
            ),
            axis=1
        )
    )


    commercial_results = (
        vendor_summary.apply(
            lambda row: (
                _score_commercial(
                    row,
                    rules
                )
            ),
            axis=1
        )
    )


    # ==================================================
    # STORE DELIVERY RESULTS
    # ==================================================

    vendor_summary[
        "delivery_prototype_score"
    ] = [
        result[0]
        for result
        in delivery_results
    ]

    vendor_summary[
        "delivery_score_status"
    ] = [
        result[1]
        for result
        in delivery_results
    ]


    # ==================================================
    # STORE QUALITY RESULTS
    # ==================================================

    vendor_summary[
        "quality_prototype_score"
    ] = [
        result[0]
        for result
        in quality_results
    ]

    vendor_summary[
        "quality_score_status"
    ] = [
        result[1]
        for result
        in quality_results
    ]


    # ==================================================
    # STORE LEAD-TIME RESULTS
    # ==================================================

    vendor_summary[
        "lead_time_prototype_score"
    ] = [
        result[0]
        for result
        in lead_time_results
    ]

    vendor_summary[
        "lead_time_score_status"
    ] = [
        result[1]
        for result
        in lead_time_results
    ]


    # ==================================================
    # STORE RESPONSIVENESS RESULTS
    # ==================================================

    vendor_summary[
        "responsiveness_prototype_score"
    ] = [
        result[0]
        for result
        in responsiveness_results
    ]

    vendor_summary[
        "responsiveness_score_status"
    ] = [
        result[1]
        for result
        in responsiveness_results
    ]


    # ==================================================
    # STORE COMMERCIAL RESULTS
    # ==================================================

    vendor_summary[
        "commercial_prototype_score"
    ] = [
        result[0]
        for result
        in commercial_results
    ]

    vendor_summary[
        "commercial_score_status"
    ] = [
        result[1]
        for result
        in commercial_results
    ]


    # ==================================================
    # COMPONENT GRADES
    # ==================================================

    grade_thresholds = (
        rules[
            "grade_thresholds"
        ]
    )


    component_score_columns = {

        "delivery_prototype_score":
            "delivery_prototype_grade",

        "quality_prototype_score":
            "quality_prototype_grade",

        "lead_time_prototype_score":
            "lead_time_prototype_grade",

        "responsiveness_prototype_score":
            "responsiveness_prototype_grade",

        "commercial_prototype_score":
            "commercial_prototype_grade"
    }


    for (
        score_column,
        grade_column
    ) in component_score_columns.items():

        vendor_summary[
            grade_column
        ] = (
            vendor_summary[
                score_column
            ]
            .apply(
                lambda score: (
                    _assign_grade(
                        score,
                        grade_thresholds
                    )
                )
            )
        )


    # ==================================================
    # OVERALL PROTOTYPE CONFIGURATION
    # ==================================================

    component_configuration = {

        "delivery_prototype_score":
            rules[
                "components"
            ][
                "on_time_delivery"
            ][
                "weight"
            ],

        "quality_prototype_score":
            rules[
                "components"
            ][
                "quality"
            ][
                "weight"
            ],

        "lead_time_prototype_score":
            rules[
                "components"
            ][
                "lead_time"
            ][
                "weight"
            ],

        "responsiveness_prototype_score":
            rules[
                "components"
            ][
                "responsiveness"
            ][
                "weight"
            ],

        "commercial_prototype_score":
            rules[
                "components"
            ][
                "commercial"
            ][
                "weight"
            ]
    }


    total_configured_weight = (
        sum(
            component_configuration.values()
        )
    )


    # ==================================================
    # CALCULATE OVERALL SCORE
    # ==================================================

    def calculate_overall_score(
        row
    ):

        weighted_score = 0.0
        available_weight = 0.0
        available_components = 0


        for (
            score_column,
            component_weight
        ) in component_configuration.items():

            component_score = (
                row[
                    score_column
                ]
            )


            if pd.notna(
                component_score
            ):

                weighted_score += (
                    component_score
                    * component_weight
                )

                available_weight += (
                    component_weight
                )

                available_components += 1


        minimum_components = (
            rules[
                "overall"
            ][
                "minimum_available_components"
            ]
        )


        if (
            available_components
            < minimum_components
            or available_weight == 0
        ):

            overall_score = (
                float("nan")
            )

            overall_status = (
                "INSUFFICIENT COMPONENT COVERAGE"
            )

        else:

            # Missing component weights are excluded
            # rather than treated as zero.

            overall_score = (
                weighted_score
                / available_weight
            )

            overall_status = (
                "SCORED"
            )


        weight_coverage_pct = (
            available_weight
            / total_configured_weight
            * 100
        )


        return pd.Series(
            {

                "prototype_scored_component_count":
                    available_components,

                "prototype_weight_coverage_pct":
                    weight_coverage_pct,

                "prototype_overall_score":
                    overall_score,

                "prototype_overall_status":
                    overall_status
            }
        )


    # ==================================================
    # APPLY OVERALL SCORE
    # ==================================================

    overall_results = (
        vendor_summary.apply(
            calculate_overall_score,
            axis=1
        )
    )


    vendor_summary = pd.concat(
        [
            vendor_summary,
            overall_results
        ],
        axis=1
    )


    # ==================================================
    # OVERALL GRADE
    # ==================================================

    vendor_summary[
        "prototype_overall_grade"
    ] = (
        vendor_summary[
            "prototype_overall_score"
        ]
        .apply(
            lambda score: (
                _assign_grade(
                    score,
                    grade_thresholds
                )
            )
        )
    )


    return vendor_summary