import numbers
from datetime import date, datetime

import pandas as pd


def _normalize_part_number(value):
    """
    Create a consistent comparison key for Part Numbers.
    The original source Part Number is not modified.
    """

    if pd.isna(value):
        return None

    if isinstance(
        value,
        (pd.Timestamp, datetime, date)
    ):
        return str(value).strip().upper()

    if isinstance(value, numbers.Number):

        numeric_value = float(value)

        if numeric_value.is_integer():
            return str(int(numeric_value))

        return str(value).strip().upper()

    cleaned_value = str(value).strip().upper()

    if cleaned_value == "":
        return None

    return cleaned_value


def _convert_lead_time(value):
    """
    Convert Item Master Lead Time into numeric days.

    Invalid or date-like values become NaN.
    """

    if pd.isna(value):
        return float("nan")

    if isinstance(
        value,
        (pd.Timestamp, datetime, date)
    ):
        return float("nan")

    numeric_value = pd.to_numeric(
        value,
        errors="coerce"
    )

    return numeric_value


def prepare_lead_time_metrics(
    purchase_df,
    items_df
):
    purchase_df = purchase_df.copy()
    items_df = items_df.copy()

    original_po_row_count = (
        purchase_df.shape[0]
    )


    # ==================================================
    # SAFE PART NUMBER MATCH KEYS
    # ==================================================

    purchase_df["part_number_match_key"] = (
        purchase_df["part_number"]
        .apply(_normalize_part_number)
    )

    items_df["part_number_match_key"] = (
        items_df["part_number"]
        .apply(_normalize_part_number)
    )


    # ==================================================
    # ITEM MASTER LEAD-TIME REFERENCE
    # ==================================================

    item_lead_time = items_df[
        [
            "part_number_match_key",
            "lead_time"
        ]
    ].copy()


    item_lead_time["item_lead_time_days"] = (
        item_lead_time["lead_time"]
        .apply(_convert_lead_time)
    )


    # Force a true numeric dtype.
    # Invalid values become NaN instead of None/object values.
    item_lead_time["item_lead_time_days"] = (
        pd.to_numeric(
            item_lead_time[
                "item_lead_time_days"
            ],
            errors="coerce"
        )
    )


    # Remove Item Master rows without a usable part key
    item_lead_time = (
        item_lead_time.loc[
            item_lead_time[
                "part_number_match_key"
            ].notna()
        ]
        .copy()
    )


    # ==================================================
    # DETECT CONFLICTING LEAD TIMES
    # ==================================================

    lead_time_variants = (
        item_lead_time
        .groupby(
            "part_number_match_key",
            sort=False
        )["item_lead_time_days"]
        .nunique(
            dropna=True
        )
    )


    conflicting_parts = (
        lead_time_variants.loc[
            lead_time_variants > 1
        ]
        .index
    )


    # If Item Master contains more than one lead time
    # for the same Part Number, do not arbitrarily choose.
    item_lead_time = (
        item_lead_time.loc[
            ~item_lead_time[
                "part_number_match_key"
            ].isin(
                conflicting_parts
            )
        ]
        .copy()
    )


    # ==================================================
    # ONE ROW PER ITEM MASTER PART
    # ==================================================

    item_lead_time_reference = (
        item_lead_time
        .groupby(
            "part_number_match_key",
            as_index=False,
            sort=False
        )
        .agg(
            item_lead_time_days=(
                "item_lead_time_days",
                "first"
            )
        )
    )


    # Make absolutely sure this is numeric
    item_lead_time_reference[
        "item_lead_time_days"
    ] = (
        pd.to_numeric(
            item_lead_time_reference[
                "item_lead_time_days"
            ],
            errors="coerce"
        )
    )


    # ==================================================
    # MERGE ITEM MASTER INTO PO TRANSACTIONS
    # ==================================================

    purchase_df = purchase_df.merge(
        item_lead_time_reference,
        on="part_number_match_key",
        how="left",
        validate="many_to_one"
    )


    if (
        purchase_df.shape[0]
        != original_po_row_count
    ):
        raise ValueError(
            "Lead-time merge changed the PO row count."
        )


    # Force merged lead time to numeric as a final safeguard
    purchase_df[
        "item_lead_time_days"
    ] = (
        pd.to_numeric(
            purchase_df[
                "item_lead_time_days"
            ],
            errors="coerce"
        )
    )


    # ==================================================
    # ACTUAL LEAD TIME
    # ==================================================

    purchase_df[
        "actual_lead_time_days"
    ] = (
        purchase_df["last_receipt_date"]
        - purchase_df["order_date"]
    ).dt.days


    purchase_df[
        "actual_lead_time_days"
    ] = (
        pd.to_numeric(
            purchase_df[
                "actual_lead_time_days"
            ],
            errors="coerce"
        )
    )


    # ==================================================
    # LEAD-TIME ELIGIBILITY
    # ==================================================

    purchase_df[
        "lead_time_eligible"
    ] = (
        purchase_df["fully_received"]
        & purchase_df["order_date"].notna()
        & purchase_df["last_receipt_date"].notna()
        & purchase_df["item_lead_time_days"].notna()
        & purchase_df["actual_lead_time_days"].notna()
        & (
            purchase_df[
                "item_lead_time_days"
            ] >= 0
        )
        & (
            purchase_df[
                "actual_lead_time_days"
            ] >= 0
        )
    )


    # ==================================================
    # LEAD-TIME VARIANCE
    # ==================================================

    purchase_df[
        "lead_time_variance_days"
    ] = float("nan")


    eligible_mask = (
        purchase_df["lead_time_eligible"]
    )


    purchase_df.loc[
        eligible_mask,
        "lead_time_variance_days"
    ] = (
        purchase_df.loc[
            eligible_mask,
            "actual_lead_time_days"
        ]
        -
        purchase_df.loc[
            eligible_mask,
            "item_lead_time_days"
        ]
    )


    # ==================================================
    # LEAD-TIME ADHERENCE
    # ==================================================

    purchase_df[
        "lead_time_adherent"
    ] = False


    purchase_df.loc[
        eligible_mask,
        "lead_time_adherent"
    ] = (
        purchase_df.loc[
            eligible_mask,
            "actual_lead_time_days"
        ]
        <=
        purchase_df.loc[
            eligible_mask,
            "item_lead_time_days"
        ]
    )


    # ==================================================
    # ELIGIBLE ACTUAL LEAD TIME
    # ==================================================

    purchase_df[
        "eligible_actual_lead_time_days"
    ] = (
        purchase_df[
            "actual_lead_time_days"
        ]
        .where(
            eligible_mask
        )
    )


    return purchase_df