from abc import ABC, abstractmethod
import pandas as pd 

#Setting up a class with a Abstract Method 

class VendorScorecardRepository(ABC):
    @abstractmethod
    def get_items(self) -> pd.DataFrame:
        pass

    @abstractmethod
    def get_purchase_orders(self) -> pd.DataFrame:
        pass

    @abstractmethod
    def get_ncrs(self) -> pd.DataFrame:
        pass

    @abstractmethod
    def get_vendors(self) -> pd.DataFrame:
        pass