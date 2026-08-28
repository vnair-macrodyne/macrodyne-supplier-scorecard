import re 
import pandas as pd



def normalize_vendor_name(name): 
    if pd.isna(name):
        return None
    cleaned_name = name.strip()
    cleaned_name = cleaned_name.upper()
    cleaned_name = re.sub(
        r"\s*\[[^\]]+\]\s*(?:\(APPROVED\))?\s*$",
        "",
        cleaned_name
    )
    cleaned_name = cleaned_name.strip()
    return cleaned_name


def extract_vendor_city(name):
    if pd.isna(name):
        return None
    match = re.search(
    r"\[([^\]]+)\]",
    name
    )

    if match: 
        city = match.group(1)
        city = city.strip().upper()
        return city

    return None


def prepare_purchase_order_vendors(purchase_df):
    purchase_df = purchase_df.copy()

    purchase_df["vendor_match_name"] = purchase_df["vendor_name"].apply(
        normalize_vendor_name
    )

    purchase_df["vendor_match_city"] = purchase_df["vendor_name"].apply(
        extract_vendor_city
    )

    return purchase_df