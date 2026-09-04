from src.services.scorecard_service import (
    build_scorecard_data
)


def get_vendor_list(search_text=""):
    """
    Return the vendor/location scorecard rows used
    by the web Vendor Scorecard page.
    """

    scorecard_data = (
        build_scorecard_data()
    )

    vendor_summary = (
        scorecard_data[
            "vendor_summary"
        ]
        .copy()
    )


    # ==================================================
    # OPTIONAL SEARCH
    # ==================================================

    search_text = (
        search_text
        .strip()
    )

    if search_text:

        search_upper = (
            search_text.upper()
        )

        vendor_mask = (
            vendor_summary[
                "vendor_match_name"
            ]
            .fillna("")
            .astype(str)
            .str.upper()
            .str.contains(
                search_upper,
                regex=False
            )
        )

        city_mask = (
            vendor_summary[
                "vendor_match_city"
            ]
            .fillna("")
            .astype(str)
            .str.upper()
            .str.contains(
                search_upper,
                regex=False
            )
        )

        vendor_summary = (
            vendor_summary.loc[
                vendor_mask
                |
                city_mask
            ]
            .copy()
        )


    # ==================================================
    # DISPLAY ORDER
    # ==================================================

    vendor_summary = (
        vendor_summary
        .sort_values(
            [
                "vendor_match_name",
                "vendor_match_city"
            ],
            na_position="last"
        )
    )


    # ==================================================
    # WEB DISPLAY COLUMNS
    # ==================================================

    columns = [
        "vendor_match_name",
        "vendor_match_city",
        "po_transaction_count",

        "delivery_prototype_grade",
        "quality_prototype_grade",
        "lead_time_prototype_grade",
        "responsiveness_prototype_grade",
        "commercial_prototype_grade",

        "prototype_overall_score",
        "prototype_overall_grade",
        "prototype_overall_status"
    ]


    vendor_summary = (
        vendor_summary[
            columns
        ]
        .copy()
    )


    return vendor_summary.to_dict(
        orient="records"
    )

def get_vendor_detail(
    vendor_name,
    vendor_city
):
    """
    Return one vendor/location scorecard record
    for the Vendor Detail page.
    """

    scorecard_data = (
        build_scorecard_data()
    )

    vendor_summary = (
        scorecard_data[
            "vendor_summary"
        ]
        .copy()
    )


    vendor_mask = (
        vendor_summary[
            "vendor_match_name"
        ]
        .fillna("")
        .astype(str)
        .eq(vendor_name)
    )


    city_mask = (
        vendor_summary[
            "vendor_match_city"
        ]
        .fillna("")
        .astype(str)
        .eq(vendor_city)
    )


    matching_rows = (
        vendor_summary.loc[
            vendor_mask
            &
            city_mask
        ]
    )


    if matching_rows.empty:
        return None


    vendor = (
        matching_rows
        .iloc[0]
        .copy()
    )


    # Convert pandas missing values to None
    # so Jinja can display them cleanly.
    vendor = (
        vendor.where(
            vendor.notna(),
            None
        )
        .to_dict()
    )


    return vendor