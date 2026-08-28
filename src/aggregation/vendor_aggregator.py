def aggregate_purchase_orders_by_vendor(purchase_df):
    purchase_df = purchase_df.copy()

    purchase_df["has_receipt"] = (
        purchase_df["received_qty"] > 0
    )
    purchase_df["fully_received"] = (
        (purchase_df["ordered_qty"] > 0)
        & (purchase_df["received_qty"] >= purchase_df["ordered_qty"])
    )

    vendor_summary = (
        purchase_df
        .groupby(
            [
                "vendor_match_name",
                "vendor_match_city"
            ],
            dropna=False
        )
        .agg(
            po_transaction_count=("po_number", "size"), #PO-line transactions
            total_ordered_qty=("ordered_qty", "sum"), # Total Quantity ordered
            total_received_qty=("received_qty", "sum"),# Total quantity received
            distinct_po_count=("po_number","nunique"), # Unique PO numbers
            received_transaction_count=("has_receipt", "sum"), # Lines with at least one receipt
            fully_received_count=("fully_received", "sum"), # Lines fully received 

            delivery_eligible_count=("delivery_eligible", "sum"),
            on_time_count=("on_time", "sum"),
            late_count=("late", "sum"),
            average_days_late=("late_days_only", "mean"),

            lead_time_eligible_count=(
                "lead_time_eligible",
                "sum"
            ),

            lead_time_adherent_count=(
                "lead_time_adherent",
                "sum"
            ),

            average_actual_lead_time_days=(
                "eligible_actual_lead_time_days",
                "mean"
            ),

            average_lead_time_variance_days=(
                "lead_time_variance_days",
                "mean"
            ),


            price_comparison_count=(
                "price_comparison_eligible",
                "sum"
            ),

            price_stable_count=(
                "price_stable",
                "sum"
            ),

            price_increase_count=(
                "price_increased",
                "sum"
            ),

            average_price_change_pct=(
                "price_change_pct",
                "mean"
            )
        )
        .reset_index()
    )


    vendor_summary["on_time_delivery_pct"] = (
        vendor_summary["on_time_count"] 
        / vendor_summary["delivery_eligible_count"]
        * 100
    )

    vendor_summary["on_time_delivery_pct"] = (
        vendor_summary["on_time_delivery_pct"]
        .where(
            vendor_summary["delivery_eligible_count"] > 0
        )
    )

    vendor_summary["lead_time_adherence_pct"] = (
        vendor_summary["lead_time_adherent_count"]
        / vendor_summary["lead_time_eligible_count"]
        * 100
    )

    vendor_summary["lead_time_adherence_pct"] = (
        vendor_summary["lead_time_adherence_pct"]
        .where(
            vendor_summary["lead_time_eligible_count"] > 0
        )
    )

    vendor_summary["price_stability_pct"] = (
        vendor_summary["price_stable_count"]
        / vendor_summary["price_comparison_count"]
        * 100
    )


    vendor_summary["price_stability_pct"] = (
        vendor_summary["price_stability_pct"]
        .where(
            vendor_summary[
                "price_comparison_count"
            ] > 0
        )
    )
    return vendor_summary