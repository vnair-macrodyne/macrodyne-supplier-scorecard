"""
repository_factory.py — choose which VendorScorecardRepository the pipeline runs on.

The DAO layer defines four methods and two implementations satisfy them, but nothing
until now let a caller pick one without editing code. This is that seam, and it is
additive: `base_repository.py` and `excel_repository.py` are untouched, because the
Excel path is the reconciliation baseline and changing it would defeat the exercise.

Selection, in precedence order:

    create_repository("eto")                      explicit argument
    python main.py --source=eto                   command line
    set SCORECARD_SOURCE=eto & python main.py     environment
    (nothing)                                     excel -- the default

The default is byte-identical to what main.py constructed before, so an unchanged
environment behaves exactly as it always did.

Resource handling is duck-typed rather than pushed into the DAO: EtoRepository holds a
database connection and defines close(); ExcelRepository holds nothing and does not.
`repository()` closes whatever is closeable, so a caller can use either safely:

    with repository() as repo:
        items = repo.get_items()
        ...
"""

import os
import sys
from contextlib import contextmanager

from .excel_repository import ExcelRepository
from .sql_repository import EtoRepository


DEFAULT_SOURCE = "excel"

EXCEL_DEFAULTS = {
    "input_dir": "data/input",
    "mapping_path": "config/column_mappings.json",
    "sources_path": "config/sources.json",
}

ETO_DEFAULTS = {
    "config_path": "config/eto.json",
}

_ALIASES = {
    "excel": "excel", "xlsx": "excel", "file": "excel",
    "eto": "eto", "sql": "eto", "db": "eto", "database": "eto",
}


def resolve_source(source=None, argv=None):
    """Work out which source to use, without importing argparse into a script."""

    if source is None:
        argv = sys.argv if argv is None else argv

        for argument in argv[1:]:
            if argument.startswith("--source="):
                source = argument.split("=", 1)[1]
                break
            if argument == "--eto":
                source = "eto"
                break

    if source is None:
        source = os.environ.get("SCORECARD_SOURCE")

    if source is None:
        source = DEFAULT_SOURCE

    key = str(source).strip().lower()

    if key not in _ALIASES:
        raise ValueError(
            f"Unknown scorecard source {source!r}. "
            f"Expected one of: {', '.join(sorted(set(_ALIASES)))}."
        )

    return _ALIASES[key]


def create_repository(source=None, argv=None, **overrides):
    """
    Build a VendorScorecardRepository.

    overrides are passed to the chosen implementation's constructor, so a caller can
    point at a different input directory or a different eto.json without changing the
    defaults here.
    """

    resolved = resolve_source(source, argv)

    if resolved == "excel":
        settings = {**EXCEL_DEFAULTS, **overrides}
        return ExcelRepository(
            settings["input_dir"],
            settings["mapping_path"],
            settings["sources_path"],
        )

    settings = {**ETO_DEFAULTS, **overrides}
    repo = EtoRepository(settings["config_path"])

    # Fail on an unresolved load-bearing column now, with the column named, rather
    # than after a clean-looking run that scored nothing. Also warns while the PO
    # scope is still unconfirmed.
    repo.check_ready()

    return repo


@contextmanager
def repository(source=None, argv=None, **overrides):
    """create_repository as a context manager that closes anything closeable."""

    repo = create_repository(source, argv, **overrides)

    try:
        yield repo
    finally:
        close = getattr(repo, "close", None)
        if callable(close):
            close()


def describe(repo):
    """One line naming the source actually in use, for the run log."""

    if isinstance(repo, EtoRepository):
        connection = repo.config["connection"]
        scope = repo.config["scope"]

        projects = scope.get("project_ids") or []

        if scope.get("po_months_back") and not scope.get("po_date_from"):
            window = f"rolling {scope['po_months_back']} months"
        else:
            window = " ".join(
                part for part in (
                    f"from {scope['po_date_from']}" if scope.get("po_date_from") else "",
                    f"to {scope['po_date_to']}" if scope.get("po_date_to") else "",
                ) if part
            )

        return (
            f"Source: ETO — {connection['database']} on {connection['server']} "
            f"(read-only)"
            f"{f', {len(projects)} project(s)' if projects else ', all projects'}"
            f"{f', {window}' if window else ''}"
            f"{'' if scope.get('scope_confirmed') else '  [SCOPE UNCONFIRMED]'}"
        )

    return f"Source: Excel — {EXCEL_DEFAULTS['input_dir']}"
