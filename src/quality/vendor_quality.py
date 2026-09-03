from difflib import SequenceMatcher

import pandas as pd


def classify_vendor_completeness(vendors_df):
    vendors_df = vendors_df.copy()

    required_fields = [
        "vendor_name",
        "address_line_1",
        "postal_code"
    ]

    field_display_names = {
        "vendor_name": "VENDOR NAME",
        "address_line_1": "ADDRESS",
        "postal_code": "POSTAL CODE"
    }

    # --------------------------------------------------
    # IDENTIFY MISSING / BLANK VALUES BY FIELD
    # --------------------------------------------------

    missing_field_mask = (
        vendors_df[required_fields].isna()
        |
        vendors_df[required_fields]
        .fillna("")
        .astype(str)
        .apply(
            lambda column:
                column.str.strip().eq("")
        )
    )

    # --------------------------------------------------
    # OVERALL COMPLETENESS STATUS
    # --------------------------------------------------

    incomplete_mask = (
        missing_field_mask.any(axis=1)
    )

    vendors_df[
        "vendor_quality_status"
    ] = "COMPLETE"

    vendors_df.loc[
        incomplete_mask,
        "vendor_quality_status"
    ] = "INCOMPLETE"

    # --------------------------------------------------
    # SPECIFIC MISSING COMPONENTS
    # --------------------------------------------------

    def get_missing_components(row):

        missing_fields = [
            field_display_names[field]
            for field in required_fields
            if row[field]
        ]

        if not missing_fields:
            return None

        return ", ".join(
            missing_fields
        )

    vendors_df[
        "missing_components"
    ] = missing_field_mask.apply(
        get_missing_components,
        axis=1
    )

    return vendors_df

def identify_exact_duplicates(vendors_df):
    vendors_df = vendors_df.copy()

    complete_mask = (
        vendors_df["vendor_quality_status"] == "COMPLETE"
    )

    vendors_df["vendor_name_key"] = (
        vendors_df["vendor_name"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    vendors_df["address_key"] = (
        vendors_df["address_line_1"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    vendors_df["postal_code_key"] = (
        vendors_df["postal_code"]
        .astype("string")
        .str.strip()
        .str.upper()
    )


    exact_duplicate_mask = (
        complete_mask
        & vendors_df.duplicated(
            subset=[
                "vendor_name_key", 
                "address_key",
                "postal_code_key"
            ],
            keep=False
        )
    )

    vendors_df["exact_duplicate_flag"] = False

    vendors_df.loc[
        exact_duplicate_mask,
        "exact_duplicate_flag",
    ] =  True

    return vendors_df

def identify_partial_duplicates(vendor_df):
    vendor_df = vendor_df.copy()

    incomplete_mask = (
        vendor_df["vendor_quality_status"] == "INCOMPLETE"
    )

    complete_mask = (
        vendor_df["vendor_quality_status"] == "COMPLETE"
    )

    incomplete_vendors = vendor_df[
        incomplete_mask
    ].copy()

    complete_vendors = vendor_df[
        complete_mask
    ].copy()

    complete_name_counts = (
        complete_vendors
        .groupby("vendor_name_key")
        .size()
        .reset_index(
            name="complete_name_candidate_count"
        )
    )

    name_candidate_analysis = incomplete_vendors.merge(
        complete_name_counts,
        on="vendor_name_key",
        how="left",
        validate="many_to_one"
    )

    name_candidate_analysis["complete_name_candidate_count"] = (
        name_candidate_analysis["complete_name_candidate_count"]
        .fillna(0)
        .astype(int)
    )

    complete_key_counts = (
        complete_vendors
        .groupby(
            ["vendor_name_key", "address_key"]
        )
        .size()
        .reset_index(
            name="complete_candidate_count"
        )
    )

    incomplete_candidate_analysis = incomplete_vendors.merge(
        complete_key_counts,
        on=[
            "vendor_name_key",
            "address_key"
        ],
        how="left",
        validate="many_to_one"
    )

    incomplete_candidate_analysis["complete_candidate_count"] = (
        incomplete_candidate_analysis["complete_candidate_count"]
        .fillna(0)
        .astype(int)
    )

    return name_candidate_analysis

def find_possible_vendor_matches(vendors_df):
    vendors_df = vendors_df.copy()

    vendors_df["city_key"] = (
        vendors_df["city"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    vendors_df["state_key"] = (
        vendors_df["state_province"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    incomplete_mask = vendors_df["vendor_quality_status"] == "INCOMPLETE"
    complete_mask = vendors_df["vendor_quality_status"] == "COMPLETE"

    incomplete_vendors = vendors_df[
        incomplete_mask
    ].copy()

    complete_vendors = vendors_df[
        complete_mask
    ].copy()

    location_ready_mask = (
        incomplete_vendors["city_key"].notna()
        & incomplete_vendors["state_key"].notna()
        & incomplete_vendors["city_key"].ne("")
        & incomplete_vendors["state_key"].ne("")
    )

    location_ready_vendors = incomplete_vendors[
        location_ready_mask
    ].copy()

    location_missing_vendors = incomplete_vendors[
        ~location_ready_mask
    ].copy()


    location_candidates = location_ready_vendors.merge(
        complete_vendors,
        on=[
            "city_key",
            "state_key"
        ],
        how="inner",
        suffixes=("_incomplete", "_complete")
    )
    location_candidates["name_similarity"] = (
        location_candidates.apply(
            lambda row: calculate_similarity(
                row["vendor_name_key_incomplete"],
                row["vendor_name_key_complete"]
            ),
            axis=1
        )
    )

    location_candidates["address_similarity"] = (
        location_candidates.apply(
            lambda row: calculate_similarity(
                row["address_key_incomplete"],
                row["address_key_complete"]
            ),
            axis=1
        )
    )
    return location_candidates


def calculate_similarity(value1, value2):
    similarity = SequenceMatcher(
        None,
        value1,
        value2
    ).ratio()

    return similarity * 100


def assign_vendor_review_status(vendors_df):
    vendors_df = vendors_df.copy()

    vendors_df["review_required"] = False 

    review_mask = (
        (vendors_df["vendor_quality_status"] == "INCOMPLETE") 
        | (vendors_df["exact_duplicate_flag"])
    )

    vendors_df.loc[
        review_mask,
        "review_required"
    ] = True

    vendors_df["review_reason"] = None

    incomplete_mask = (
        vendors_df["vendor_quality_status"] == "INCOMPLETE"
    )
    duplicate_mask = (
        vendors_df["exact_duplicate_flag"]
    )

    vendors_df.loc[
        incomplete_mask,
        "review_reason"
    ] = (
        "MISSING: "
        +
        vendors_df.loc[
            incomplete_mask,
            "missing_components"
        ]
    )
    vendors_df.loc[
        duplicate_mask,
        "review_reason",
    ] = "EXACT DUPLICATE"

    return vendors_df