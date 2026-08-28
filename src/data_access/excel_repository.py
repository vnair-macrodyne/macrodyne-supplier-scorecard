# . means import this from the current Python Package 
from .base_repository import VendorScorecardRepository
from pathlib import Path
import pandas as pd
import json

class ExcelRepository(VendorScorecardRepository):
    def __init__(self, input_dir, mapping_path, sources_path):
        #Location from where we will use our resource
        self.input_dir = Path(input_dir)
        self.mapping_path = Path(mapping_path)
        self.sources_path = Path(sources_path)
        #Open the configuration file
        with open(self.mapping_path, "r") as file:
            self.mappings = json.load(file)

        with open(self.sources_path, "r") as file1:
            self.sources = json.load(file1)
    #The leading _ is a python convention: This is an internal helper for the class, not something the rest of the application calls directly 
    def _load_dataset(self, dataset_key) -> pd.DataFrame:
        filename = self.sources[dataset_key]["filename"]
        #Build the file path
        file_path = self.input_dir / filename
        #Read Excel
        file_df = pd.read_excel(file_path)
        #Set function to understand what the expected columns and actual columns are
        expected_columns = set(self.mappings[dataset_key].keys())
        actual_columns = set(file_df.columns)
        missing_columns = set.difference(expected_columns, actual_columns)
        if missing_columns:
            raise ValueError(f"Missing required columns for {dataset_key}: {missing_columns}")
        #Rename the columns
        file_df = file_df.rename(columns=self.mappings[dataset_key])
        #Return Dataframe
        return file_df


    def _validate_purchase_order_types(self, po_df):
        numeric_columns = ["ordered_qty",
                           "received_qty",
                           "unit_price",
                           "extended_value"]
        for column in numeric_columns:
            if not pd.api.types.is_numeric_dtype(po_df[column]):
                raise ValueError(f"Invalid datatype for {column}: expected numeric")

        date_columns = [
            "order_date", 
            "required_date", 
            "revised_date", 
            "last_receipt_date"
        ]

        for column in date_columns:
            if not pd.api.types.is_datetime64_any_dtype(po_df[column]):
                raise ValueError(f"Invalid datatype for {column}: expected datetime")

    def get_items(self):
        return self._load_dataset("items")


    def get_purchase_orders(self) -> pd.DataFrame:
        po_df = self._load_dataset("purchase_orders")

        required_columns = ["po_number", 
                            "vendor_name", 
                            "part_number", 
                            "ordered_qty", 
                            "order_date"]


        
        header_rows = (po_df["part_number"].isna() &
                       po_df["vendor_name"].isna() &
                       po_df["ordered_qty"].isna() &
                       po_df["order_date"].isna())

        po_df = po_df[~header_rows]

        incomplete_rows = po_df[required_columns].isna().any(axis=1)
        invalid_rows = po_df[incomplete_rows].copy()
        valid_rows = po_df[~incomplete_rows].copy()
        print(f"Valid PO rows: {valid_rows.shape[0]}")
        print(f"Invalid PO rows: {invalid_rows.shape[0]}")
        
        self.rejected_purchase_orders = invalid_rows
        po_df = valid_rows
        self._validate_purchase_order_types(po_df)
        return po_df
    


    def get_ncrs(self):
        return self._load_dataset("ncrs")


        
    def get_vendors(self) -> pd.DataFrame:
        vendor_df = self._load_dataset("vendors")
        return vendor_df