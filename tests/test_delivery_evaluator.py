import pandas as pd

from src.evaluation.delivery_evaluator import prepare_delivery_metrics


def test_prepare_delivery_metrics_classifies_delivery_rows_correctly():
    purchase_orders = pd.DataFrame(
        {
            "ordered_qty": [10, 10, 10, 10],
            "received_qty": [10, 10, 5, 10],

            "required_date": pd.to_datetime(
                [
                    "2026-01-10",
                    "2026-01-10",
                    "2026-01-10",
                    "2026-01-10"
                ]
            ),

            "revised_date": pd.to_datetime(
                [
                    None,
                    None,
                    None,
                    "2026-01-15"
                ]
            ),

            "last_receipt_date": pd.to_datetime(
                [
                    "2026-01-08",
                    "2026-01-12",
                    "2026-01-09",
                    "2026-01-14"
                ]
            )
        }
    )

    result = prepare_delivery_metrics(
        purchase_orders
    )

    # --------------------------------------------------
    # ROW 1
    # Fully received and delivered before Required Date.
    # --------------------------------------------------

    assert result.loc[0, "delivery_eligible"]
    assert result.loc[0, "on_time"]
    assert not result.loc[0, "late"]
    assert result.loc[0, "days_late"] == 0


    # --------------------------------------------------
    # ROW 2
    # Fully received but delivered after Required Date.
    # --------------------------------------------------

    assert result.loc[1, "delivery_eligible"]
    assert not result.loc[1, "on_time"]
    assert result.loc[1, "late"]
    assert result.loc[1, "days_late"] == 2


    # --------------------------------------------------
    # ROW 3
    # Not fully received, so it is not delivery eligible.
    # --------------------------------------------------

    assert not result.loc[2, "delivery_eligible"]
    assert not result.loc[2, "on_time"]
    assert not result.loc[2, "late"]


    # --------------------------------------------------
    # ROW 4
    # Revised Date overrides Required Date.
    #
    # Required Date = Jan 10
    # Revised Date  = Jan 15
    # Receipt Date  = Jan 14
    #
    # Therefore this should be ON TIME.
    # --------------------------------------------------

    assert result.loc[3, "target_date"] == pd.Timestamp(
        "2026-01-15"
    )

    assert result.loc[3, "delivery_eligible"]
    assert result.loc[3, "on_time"]
    assert not result.loc[3, "late"]
    assert result.loc[3, "days_late"] == 0