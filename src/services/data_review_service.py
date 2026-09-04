from src.services.scorecard_service import (
    get_fresh_scorecard_data
)


def _available_columns(
    dataframe,
    preferred_columns,
    fallback_count=8
):
    """
    Return preferred columns that actually exist
    in the dataframe.

    If none are available, return the first few
    available dataframe columns.
    """

    columns = [
        column
        for column in preferred_columns
        if column in dataframe.columns
    ]

    if columns:
        return columns

    return list(
        dataframe.columns[
            :fallback_count
        ]
    )


def get_data_review():
    """
    Return vendor-master review records and
    unmatched supplier NCR records for the
    Data Review web page.
    """

    scorecard_data = (
        get_fresh_scorecard_data()
    )

    vendors = (
        scorecard_data[
            "vendors"
        ]
        .copy()
    )

    unmatched_ncrs = (
        scorecard_data[
            "unmatched_ncrs"
        ]
        .copy()
    )


    # ==================================================
    # VENDOR REVIEW
    # ==================================================

    vendor_review = (
        vendors.loc[
            vendors[
                "review_required"
            ]
        ]
        .copy()
    )


    preferred_vendor_columns = [
        "vendor_name",
        "city",
        "address_line_1",
        "postal_code",
        "missing_components",
        "review_reason"
    ]


    vendor_review_columns = (
        _available_columns(
            vendor_review,
            preferred_vendor_columns
        )
    )


    vendor_review_rows = (
        vendor_review[
            vendor_review_columns
        ]
        .to_dict(
            orient="records"
        )
    )


    # ==================================================
    # UNMATCHED NCR REVIEW
    # ==================================================

    preferred_ncr_columns = [
        "ncr_number",
        "vendor_match_name",
        "vendor_match_city",
        "supplier",
        "part_number",
        "status",
        "po_location_count"
    ]


    unmatched_ncr_columns = (
        _available_columns(
            unmatched_ncrs,
            preferred_ncr_columns
        )
    )


    unmatched_ncr_rows = (
        unmatched_ncrs[
            unmatched_ncr_columns
        ]
        .to_dict(
            orient="records"
        )
    )


    # ==================================================
    # SUMMARY
    # ==================================================

    return {
        "vendor_review_count":
            len(vendor_review_rows),

        "unmatched_ncr_count":
            len(unmatched_ncr_rows),

        "vendor_review_columns":
            vendor_review_columns,

        "vendor_review_rows":
            vendor_review_rows,

        "unmatched_ncr_columns":
            unmatched_ncr_columns,

        "unmatched_ncr_rows":
            unmatched_ncr_rows
    }