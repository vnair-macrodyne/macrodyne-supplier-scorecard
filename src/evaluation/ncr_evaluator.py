import pandas as pd

from src.matching.vendor_matcher import (
    normalize_vendor_name,
    extract_vendor_city
)


def prepare_ncr_metrics(ncr_df):
    """
    Prepare NCR transaction-level fields used by the Vendor Scorecard.

    Important:
    - supplier_linked means an NCR contains a supplier.
    - It does NOT mean the supplier has been confirmed responsible.
    - Quality calculations exclude invalid quantity relationships.
    - NCR resolution is currently used only as a prototype
      responsiveness proxy.
    """

    ncr_df = ncr_df.copy()


    # ==================================================
    # VENDOR / SUPPLIER PREPARATION
    # ==================================================

    ncr_df["vendor_match_name"] = (
        ncr_df["vendor_name"]
        .apply(normalize_vendor_name)
    )

    ncr_df["vendor_match_city"] = (
        ncr_df["vendor_name"]
        .apply(extract_vendor_city)
    )

    ncr_df["supplier_linked"] = (
        ncr_df["vendor_name"].notna()
    )


    # ==================================================
    # NCR QUANTITY PREPARATION
    # ==================================================

    ncr_df["quantity"] = (
        pd.to_numeric(
            ncr_df["quantity"],
            errors="coerce"
        )
    )

    ncr_df["quantity_rejected"] = (
        pd.to_numeric(
            ncr_df["quantity_rejected"],
            errors="coerce"
        )
    )


    # ==================================================
    # NCR QUANTITY ANOMALY
    # ==================================================
    #
    # Example:
    # NCR Quantity = 12
    # Qty Rejected = 60
    #
    # We flag this rather than silently using it in
    # the rejected percentage.
    # ==================================================

    ncr_df["ncr_quantity_anomaly"] = (
        ncr_df["quantity"].notna()
        & ncr_df["quantity_rejected"].notna()
        & (
            ncr_df["quantity_rejected"]
            > ncr_df["quantity"]
        )
    )


    # ==================================================
    # QUALITY ELIGIBILITY
    # ==================================================
    #
    # A row is eligible for NCR Rejected % when:
    #
    # - supplier is linked
    # - NCR quantity exists
    # - rejected quantity exists
    # - NCR quantity > 0
    # - rejected quantity >= 0
    # - rejected quantity does not exceed NCR quantity
    #
    # ==================================================

    ncr_df["quality_eligible"] = (
        ncr_df["supplier_linked"]
        & ncr_df["quantity"].notna()
        & ncr_df["quantity_rejected"].notna()
        & (
            ncr_df["quantity"] > 0
        )
        & (
            ncr_df["quantity_rejected"] >= 0
        )
        & ~ncr_df["ncr_quantity_anomaly"]
    )


    # Keep quantity only for valid Quality rows
    ncr_df["quality_quantity"] = (
        ncr_df["quantity"]
        .where(
            ncr_df["quality_eligible"]
        )
    )


    # Keep rejected quantity only for valid Quality rows
    ncr_df["quality_rejected_quantity"] = (
        ncr_df["quantity_rejected"]
        .where(
            ncr_df["quality_eligible"]
        )
    )


    # ==================================================
    # RESPONSIVENESS PROXY PREPARATION
    # ==================================================
    #
    # We do not currently have:
    #
    # supplier request timestamp
    # supplier response timestamp
    #
    # Therefore NCR resolution is being used only as
    # a PROTOTYPE responsiveness proxy.
    # ==================================================

    ncr_df["responsiveness_eligible"] = (
        ncr_df["supplier_linked"]
        & ncr_df["resolved"].notna()
    )


    ncr_df["resolved_flag"] = (
        ncr_df["responsiveness_eligible"]
        & ncr_df["resolved"].eq(True)
    )


    ncr_df["unresolved_flag"] = (
        ncr_df["responsiveness_eligible"]
        & ncr_df["resolved"].eq(False)
    )


    return ncr_df