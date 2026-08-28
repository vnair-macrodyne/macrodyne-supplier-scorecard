def prepare_delivery_metrics(purchase_df):
    purchase_df = purchase_df.copy()

    purchase_df["target_date"] = purchase_df["revised_date"].fillna(
        purchase_df["required_date"]
    )

    purchase_df["fully_received"] = (
        (purchase_df["ordered_qty"] > 0)
        & (purchase_df["received_qty"] >= purchase_df["ordered_qty"])
    )

    purchase_df["delivery_eligible"] = (
        purchase_df["fully_received"]
        & purchase_df["target_date"].notna()
        & purchase_df["last_receipt_date"].notna()
    )

    delivery_variance = (
        purchase_df["last_receipt_date"] - purchase_df["target_date"]
    ).dt.days

    purchase_df["on_time"] = (
        purchase_df["delivery_eligible"]
        & (
            purchase_df["last_receipt_date"] <= purchase_df["target_date"]
        )
    )

    purchase_df["late"] = (
        purchase_df["delivery_eligible"] & (purchase_df["last_receipt_date"] > purchase_df["target_date"])
    )

    purchase_df["days_late"] = (
        delivery_variance
        .clip(lower=0)
        .where(purchase_df["delivery_eligible"])
    )

    purchase_df["late_days_only"] = (
        purchase_df["days_late"].where(
            purchase_df["late"]
        )
    )



    return purchase_df