def aggregate_ncrs_by_vendor(ncr_df):
    """
    Aggregate matched supplier-linked NCR transactions
    to Vendor + Location level.

    The input should already contain only NCRs that have
    been safely matched to a PO Vendor + Location.
    """

    ncr_df = ncr_df.copy()


    # ==================================================
    # NCR VENDOR AGGREGATION
    # ==================================================

    ncr_summary = (
        ncr_df
        .groupby(
            [
                "vendor_match_name",
                "vendor_match_city"
            ],
            dropna=False
        )
        .agg(

            # ------------------------------------------
            # BASIC NCR ACTIVITY
            # ------------------------------------------

            supplier_linked_ncr_count=(
                "ncr_number",
                "size"
            ),


            # Raw rejected quantity.
            #
            # We retain this existing metric for context,
            # even though NCR Rejected % below uses only
            # validated Quality-eligible rows.
            total_rejected_qty=(
                "quantity_rejected",
                lambda values: (
                    values.sum(
                        min_count=1
                    )
                )
            ),


            # ------------------------------------------
            # QUALITY METRICS
            # ------------------------------------------

            quality_eligible_ncr_count=(
                "quality_eligible",
                "sum"
            ),

            total_ncr_quantity=(
                "quality_quantity",
                lambda values: (
                    values.sum(
                        min_count=1
                    )
                )
            ),

            quality_rejected_qty=(
                "quality_rejected_quantity",
                lambda values: (
                    values.sum(
                        min_count=1
                    )
                )
            ),

            ncr_quantity_anomaly_count=(
                "ncr_quantity_anomaly",
                "sum"
            ),


            # ------------------------------------------
            # RESPONSIVENESS PROXY
            # ------------------------------------------

            responsiveness_eligible_ncr_count=(
                "responsiveness_eligible",
                "sum"
            ),

            resolved_ncr_count=(
                "resolved_flag",
                "sum"
            ),

            unresolved_ncr_count=(
                "unresolved_flag",
                "sum"
            )
        )
        .reset_index()
    )


    # ==================================================
    # QUALITY - NCR REJECTED %
    # ==================================================
    #
    # Quality Rejected %
    #
    #     Valid Rejected Quantity
    #     ----------------------- x 100
    #       Valid NCR Quantity
    #
    # Lower is better.
    # ==================================================

    ncr_summary["ncr_rejected_pct"] = (
        ncr_summary["quality_rejected_qty"]
        / ncr_summary["total_ncr_quantity"]
        * 100
    )


    # No valid denominator = no Quality percentage
    ncr_summary["ncr_rejected_pct"] = (
        ncr_summary["ncr_rejected_pct"]
        .where(
            ncr_summary[
                "total_ncr_quantity"
            ] > 0
        )
    )


    # ==================================================
    # RESPONSIVENESS PROXY %
    # ==================================================
    #
    # Prototype only:
    #
    #        Resolved NCRs
    #     ------------------ x 100
    #     NCRs with known
    #     resolution status
    #
    # Higher is better.
    #
    # This is NOT true vendor response-time performance.
    # ==================================================

    ncr_summary[
        "responsiveness_proxy_pct"
    ] = (
        ncr_summary[
            "resolved_ncr_count"
        ]
        / ncr_summary[
            "responsiveness_eligible_ncr_count"
        ]
        * 100
    )


    ncr_summary[
        "responsiveness_proxy_pct"
    ] = (
        ncr_summary[
            "responsiveness_proxy_pct"
        ]
        .where(
            ncr_summary[
                "responsiveness_eligible_ncr_count"
            ] > 0
        )
    )


    return ncr_summary