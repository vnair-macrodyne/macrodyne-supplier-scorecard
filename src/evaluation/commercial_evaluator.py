import pandas as pd


def prepare_commercial_metrics(purchase_df):
    """
    Prepare transaction-level commercial performance metrics.

    Prototype definition:

    Compare a vendor's current unit price with the previous
    purchase price for the same:

        Vendor
        Location
        Part
        Currency
        UOM

    This avoids comparing prices across different currencies
    or different parts.

    Price Stability:
        Current price <= previous price

    Average Price Change %:
        (Current - Previous) / Previous * 100
    """

    purchase_df = purchase_df.copy()


    # ==================================================
    # PRESERVE ORIGINAL ROW ORDER
    # ==================================================

    purchase_df["_commercial_row_order"] = range(
        purchase_df.shape[0]
    )


    # ==================================================
    # COMMERCIAL FIELD PREPARATION
    # ==================================================

    purchase_df["unit_price"] = pd.to_numeric(
        purchase_df["unit_price"],
        errors="coerce"
    )


    purchase_df["commercial_currency_key"] = (
        purchase_df["currency_code"]
        .astype("string")
        .str.strip()
        .str.upper()
    )


    purchase_df["commercial_uom_key"] = (
        purchase_df["uom"]
        .astype("string")
        .str.strip()
        .str.upper()
    )


    purchase_df["commercial_part_key"] = (
        purchase_df["part_number"]
        .astype("string")
        .str.strip()
        .str.upper()
    )


    # ==================================================
    # BASE COMMERCIAL ELIGIBILITY
    # ==================================================
    #
    # Before a repeat-price comparison can be performed,
    # the transaction must have:
    #
    # - vendor
    # - part
    # - currency
    # - UOM
    # - order date
    # - positive unit price
    #
    # ==================================================

    purchase_df["commercial_base_eligible"] = (
        purchase_df["vendor_match_name"].notna()
        & purchase_df["commercial_part_key"].notna()
        & purchase_df["commercial_currency_key"].notna()
        & purchase_df["commercial_uom_key"].notna()
        & purchase_df["order_date"].notna()
        & purchase_df["unit_price"].notna()
        & (
            purchase_df["unit_price"] > 0
        )
    )


    # ==================================================
    # SORT FOR PRICE HISTORY
    # ==================================================

    commercial_group_columns = [
        "vendor_match_name",
        "vendor_match_city",
        "commercial_part_key",
        "commercial_currency_key",
        "commercial_uom_key"
    ]


    purchase_df = purchase_df.sort_values(
        by=[
            *commercial_group_columns,
            "order_date",
            "_commercial_row_order"
        ],
        na_position="last"
    ).copy()


    # ==================================================
    # PREVIOUS PURCHASE PRICE
    # ==================================================

    purchase_df["previous_unit_price"] = (
        purchase_df
        .groupby(
            commercial_group_columns,
            dropna=False
        )["unit_price"]
        .shift(1)
    )


    # ==================================================
    # PRICE COMPARISON ELIGIBILITY
    # ==================================================

    purchase_df["price_comparison_eligible"] = (
        purchase_df["commercial_base_eligible"]
        & purchase_df["previous_unit_price"].notna()
        & (
            purchase_df["previous_unit_price"] > 0
        )
    )


    # ==================================================
    # PRICE CHANGE %
    # ==================================================

    purchase_df["price_change_pct"] = float("nan")


    eligible_mask = (
        purchase_df["price_comparison_eligible"]
    )


    purchase_df.loc[
        eligible_mask,
        "price_change_pct"
    ] = (
        (
            purchase_df.loc[
                eligible_mask,
                "unit_price"
            ]
            -
            purchase_df.loc[
                eligible_mask,
                "previous_unit_price"
            ]
        )
        /
        purchase_df.loc[
            eligible_mask,
            "previous_unit_price"
        ]
        * 100
    )


    # ==================================================
    # PRICE STABILITY
    # ==================================================
    #
    # Prototype rule:
    #
    # Current unit price <= previous unit price
    # means the comparison is price-stable.
    #
    # ==================================================

    purchase_df["price_stable"] = False


    purchase_df.loc[
        eligible_mask,
        "price_stable"
    ] = (
        purchase_df.loc[
            eligible_mask,
            "unit_price"
        ]
        <=
        purchase_df.loc[
            eligible_mask,
            "previous_unit_price"
        ]
    )


    purchase_df["price_increased"] = False


    purchase_df.loc[
        eligible_mask,
        "price_increased"
    ] = (
        purchase_df.loc[
            eligible_mask,
            "unit_price"
        ]
        >
        purchase_df.loc[
            eligible_mask,
            "previous_unit_price"
        ]
    )


    # ==================================================
    # RESTORE ORIGINAL PO ORDER
    # ==================================================

    purchase_df = (
        purchase_df
        .sort_values(
            "_commercial_row_order"
        )
        .drop(
            columns=[
                "_commercial_row_order"
            ]
        )
        .reset_index(
            drop=True
        )
    )


    return purchase_df