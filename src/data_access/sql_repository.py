"""
sql_repository.py — the ETO implementation of VendorScorecardRepository.

This is the whole point of the repository abstraction. Nothing downstream of this class
knows the data stopped coming from Excel: the four methods return DataFrames with exactly
the same column names, dtypes and cleaning rules as ExcelRepository, so main.py and every
evaluator, aggregator and scorer run untouched.

Parity is deliberate and load-bearing. Until a SQL run reconciles against an Excel run
row-for-row, we cannot tell a migration bug from a metric improvement -- so this class
reproduces the Excel behaviour including its quirks, and improvements land afterwards as
their own reviewable change.

The two places parity requires active work:

  * Type contract. Excel handed pandas its own typing; SQL Server hands us decimals and
    datetimes through pyodbc. Numeric and date columns are coerced explicitly here so
    _validate_purchase_order_types passes on exactly the same basis.

  * Truth values. ncr_evaluator compares resolution with .eq(True) / .eq(False).
    A bit read through pyodbc arrives as a Python bool and compares correctly, but a
    TEXT '1'/'0' or 'Yes'/'No' satisfies notna() while failing both tests -- silently
    reporting every vendor as 0% responsive. Coerced here, and unmappable values raise.

One deliberate parity EXCEPTION is documented at its site in get_purchase_orders:
received_qty is filled to 0 where no receipt exists.

Read-only throughout: the connection is opened with autocommit and only SELECT statements
are ever issued.
"""

import os

import pandas as pd

from .base_repository import VendorScorecardRepository
from .eto_queries import (
    load_eto_config,
    build_purchase_order_sql,
    build_ncr_sql,
    build_item_sql,
    build_vendor_sql,
    unresolved_columns,
)


# Columns coerced before the PO type contract is checked. Mirrors
# ExcelRepository._validate_purchase_order_types.
_PO_NUMERIC = ("ordered_qty", "received_qty", "unit_price", "extended_value")
_PO_DATETIME = ("order_date", "required_date", "revised_date", "last_receipt_date")

_PO_REQUIRED = ("po_number", "vendor_name", "part_number", "ordered_qty", "order_date")

_NCR_NUMERIC = ("quantity", "quantity_rejected", "ncr_costs", "ncr_hours",
                "total_tasks", "outstanding_tasks")
_NCR_DATETIME = ("created_date", "released", "target_date", "date_follow_up")
_NCR_BOOLEAN = ("resolved",)

_ITEM_NUMERIC = ("lead_time", "list_price", "lpp", "quantity_on_hand", "quantity_reserved")


class EtoRepository(VendorScorecardRepository):
    """Read-only Total ETO source for the Vendor Scorecard."""

    def __init__(self, config_path="config/eto.json", connection=None):
        self.config = load_eto_config(config_path)
        self._external_connection = connection
        self._connection = connection

        # Populated by get_purchase_orders, same contract as ExcelRepository.
        self.rejected_purchase_orders = None

        # Populated on each load so the caller can report what SQL actually ran.
        self.last_row_counts = {}

    # ==================================================
    # CONNECTION
    # ==================================================

    def _resolve_auth_mode(self):
        """
        Resolve ETO authentication mode.

        ETO_AUTH_MODE may override config/eto.json.
        Supported values: windows, sql.
        """

        environment_mode = os.environ.get("ETO_AUTH_MODE")

        if environment_mode:
            auth_mode = environment_mode.strip().lower()

            if auth_mode not in {"windows", "sql"}:
                raise ValueError(
                    "Invalid ETO_AUTH_MODE. Expected 'windows' or 'sql'."
                )

            return auth_mode

        if self.config["connection"].get("use_windows_auth"):
            return "windows"

        return "sql"


    def connect(self):
        """Open a read-only pyodbc connection, or reuse an injected one."""

        if self._connection is not None:
            return self._connection

        import pyodbc

        conn = self.config["connection"]
        auth_mode = self._resolve_auth_mode()

        parts = [
            f"Driver={{{conn['driver']}}}",
            f"Server={conn['server']}",
            f"Database={conn['database']}",
        ]

        if auth_mode == "windows":
            parts.append("Trusted_Connection=yes")
        else:
            user = os.environ.get(conn.get("username_env", "ETO_USER"))
            password = os.environ.get(conn.get("password_env", "ETO_PWD"))

            if not user or not password:
                raise ValueError(
                    "ETO credentials not found. Set the environment variables named by "
                    f"connection.username_env ({conn.get('username_env')}) and "
                    f"connection.password_env ({conn.get('password_env')}), or set "
                    "connection.use_windows_auth to true."
                )

            parts.append(f"UID={user}")
            parts.append(f"PWD={password}")

        self._connection = pyodbc.connect(
            ";".join(parts) + ";",
            timeout=conn.get("login_timeout_seconds", 15),
            autocommit=True,
        )

        self._connection.timeout = conn.get("query_timeout_seconds", 300)

        return self._connection

    def close(self):
        """Close the connection unless it was injected by the caller."""

        if self._connection is not None and self._external_connection is None:
            self._connection.close()

        self._connection = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    # ==================================================
    # QUERY EXECUTION
    # ==================================================

    def _read(self, dataset, sql, params):
        connection = self.connect()

        frame = pd.read_sql_query(sql, connection, params=params or None)

        self.last_row_counts[dataset] = frame.shape[0]

        return frame

    # ==================================================
    # COERCION HELPERS
    # ==================================================

    @staticmethod
    def _coerce_numeric(frame, columns):
        for column in columns:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame

    @staticmethod
    def _coerce_datetime(frame, columns):
        for column in columns:
            if column in frame.columns:
                frame[column] = pd.to_datetime(frame[column], errors="coerce")
        return frame

    _TRUE_TOKENS = {"1", "true", "yes", "y", "t"}
    _FALSE_TOKENS = {"0", "false", "no", "n", "f"}

    @classmethod
    def _coerce_boolean(cls, frame, columns):
        """
        Normalise a truth column to a nullable boolean, or fail loudly.

        ncr_evaluator builds responsiveness_eligible from .notna() and then compares
        with .eq(True) / .eq(False). Python bools, ints and Decimals all compare
        correctly, so a bit column read through pyodbc needs no help. TEXT does not:
        '1'/'0' or 'Yes'/'No' satisfy notna() but fail both equality tests, so every
        vendor reports 0% responsive against a healthy-looking eligibility count --
        a silent wrong answer, which is the worst kind.

        Anything that cannot be mapped raises instead of passing through, because a
        startup error naming the offending values is recoverable and a quietly dead
        component is not.
        """

        for column in columns:
            if column not in frame.columns:
                continue

            series = frame[column]

            if pd.api.types.is_bool_dtype(series):
                frame[column] = series.astype("boolean")
                continue

            numeric = pd.to_numeric(series, errors="coerce")
            coerced = numeric.map({1: True, 0: False})

            # Whatever numeric coercion could not place, try as text.
            text_mask = coerced.isna() & series.notna()

            if text_mask.any():
                tokens = (
                    series[text_mask]
                    .astype(str)
                    .str.strip()
                    .str.lower()
                )

                coerced.loc[text_mask] = tokens.map(
                    lambda token: (
                        True if token in cls._TRUE_TOKENS
                        else False if token in cls._FALSE_TOKENS
                        else None
                    )
                )

            unmapped = series[coerced.isna() & series.notna()]

            if not unmapped.empty:
                raise ValueError(
                    f"Column '{column}' holds values that are neither true nor false: "
                    f"{sorted(set(unmapped.astype(str)))[:8]}. Map them in "
                    f"EtoRepository._TRUE_TOKENS / _FALSE_TOKENS, or fix the source "
                    f"expression in config/eto.json."
                )

            frame[column] = coerced.astype("boolean")

        return frame

    def _validate_purchase_order_types(self, po_df):
        """Identical contract to ExcelRepository._validate_purchase_order_types."""

        for column in _PO_NUMERIC:
            if not pd.api.types.is_numeric_dtype(po_df[column]):
                raise ValueError(
                    f"Invalid datatype for {column}: expected numeric"
                )

        for column in _PO_DATETIME:
            if not pd.api.types.is_datetime64_any_dtype(po_df[column]):
                raise ValueError(
                    f"Invalid datatype for {column}: expected datetime"
                )

    # ==================================================
    # REPOSITORY METHODS
    # ==================================================

    def get_purchase_orders(self):
        """
        PO lines, cleaned to the same contract as the Excel path.

        The Excel loader also stripped report header rows -- rows where part number,
        vendor, quantity and order date were all null. That artifact does not exist in
        SQL, so the step is absent here by design rather than by omission; the
        required-field split that follows it is reproduced exactly.
        """

        sql, params = build_purchase_order_sql(self.config)
        po_df = self._read("purchase_orders", sql, params)

        # The split runs on RAW values, before coercion, exactly as ExcelRepository does.
        # Coercing first would turn an unparseable value into NaN and move its row into
        # the rejected set on a basis the Excel path never applied -- a difference in the
        # one function the reconciliation depends on.
        incomplete_rows = po_df[list(_PO_REQUIRED)].isna().any(axis=1)

        self.rejected_purchase_orders = po_df[incomplete_rows].copy()
        po_df = po_df[~incomplete_rows].copy()

        print(f"Valid PO rows: {po_df.shape[0]}")
        print(f"Invalid PO rows: {self.rejected_purchase_orders.shape[0]}")

        po_df = self._coerce_numeric(po_df, _PO_NUMERIC + ("currency_rate",))
        po_df = self._coerce_datetime(po_df, _PO_DATETIME + ("receiving_date",))

        # PARITY EXCEPTION, stated in docs/ETO_MAPPING.md section 1.2.
        # A line with no receiver-log row has a NULL receipt quantity here. Whether the
        # Excel export carried 0 or blank for the same line is not established, so this
        # is a deliberate choice rather than a reproduction: left as NULL, received_qty
        # fails the >= comparison, fully_received is False on every line, and On-Time
        # Delivery scores nothing at all. Treating "no receipt recorded" as zero received
        # is the only reading that keeps the component alive, and it is the same reading
        # ETO's own late report takes: ISNULL(SumOfQtyReceived, 0).
        po_df["received_qty"] = po_df["received_qty"].fillna(0)

        self._validate_purchase_order_types(po_df)

        return po_df

    def get_ncrs(self):
        sql, params = build_ncr_sql(self.config)
        ncr_df = self._read("ncrs", sql, params)

        ncr_df = self._coerce_numeric(ncr_df, _NCR_NUMERIC)
        ncr_df = self._coerce_datetime(ncr_df, _NCR_DATETIME)
        ncr_df = self._coerce_boolean(ncr_df, _NCR_BOOLEAN)

        return ncr_df

    def get_items(self):
        sql, params = build_item_sql(self.config)
        items_df = self._read("items", sql, params)

        return self._coerce_numeric(items_df, _ITEM_NUMERIC)

    def get_vendors(self):
        sql, params = build_vendor_sql(self.config)

        return self._read("vendors", sql, params)

    # ==================================================
    # PRE-FLIGHT
    # ==================================================

    def preflight(self):
        """
        Report what is unresolved before a run, rather than after a silent bad result.

        Returns {dataset: (unresolved column names,)}. Call it at startup and refuse to
        score if a load-bearing column is still null -- 'uom' kills Commercial, and
        'quantity'/'quantity_rejected' kill NCR Rejected %.
        """

        report = {}

        for dataset in ("purchase_orders", "ncrs", "items", "vendors"):
            missing = unresolved_columns(self.config, dataset)

            if missing:
                report[dataset] = missing

        return report

    # Columns where a null expression does NOT degrade gracefully.
    #
    #   The five _PO_REQUIRED names are the required-field set: a null there sends every
    #   row to rejected_purchase_orders, the type validation passes on an empty frame,
    #   and the pipeline completes cheerfully with a zero-row scorecard.
    #
    #   uom       -> commercial_base_eligible is false everywhere; Commercial (20%) dies
    #   quantity /
    #   quantity_rejected -> quality_eligible is false everywhere; NCR Rejected % dies
    #
    # part_number is doubly load-bearing: it is a required field AND the lead-time match
    # key, and the same expression feeds the items dataset.
    LOAD_BEARING = {
        "purchase_orders": _PO_REQUIRED + ("uom",),
        "ncrs": ("quantity", "quantity_rejected"),
        "items": ("part_number",),
    }

    def blocking_gaps(self):
        """Unresolved columns that would empty a dataset or silently kill a component."""

        blocking = {}

        for dataset, critical in self.LOAD_BEARING.items():
            missing = set(unresolved_columns(self.config, dataset))
            hits = tuple(column for column in critical if column in missing)

            if hits:
                blocking[dataset] = hits

        return blocking

    def check_ready(self, strict=True):
        """
        Fail before a run rather than after a meeting.

        Raises when a load-bearing column is unresolved; warns when the PO scope has
        not been confirmed, because the shipped scope values are a placeholder and a
        run against the wrong population produces a diff that means nothing.
        """

        blocking = self.blocking_gaps()

        if blocking and strict:
            detail = "; ".join(
                f"{dataset}: {', '.join(columns)}"
                for dataset, columns in blocking.items()
            )
            raise ValueError(
                f"Load-bearing columns are unresolved in config/eto.json -- {detail}. "
                f"Run tools/eto_schema_probe.py and set them before scoring."
            )

        if not self.config["scope"].get("scope_confirmed"):
            print(
                "\n*** WARNING: config/eto.json scope.scope_confirmed is false.\n"
                "    The shipped scope values are a placeholder, not the Excel extract's\n"
                "    scope. Settle it from probe section D before trusting any result.\n"
            )

        return blocking
