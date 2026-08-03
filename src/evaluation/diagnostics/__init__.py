"""Summary-only diagnostics for the B2 structured-report evaluation."""

from .b2 import (
    CATEGORY_ORDER,
    DEFECT_COUNT_BUCKETS,
    SUMMARY_FIELDS,
    diagnose,
    diagnose_files,
    diagnose_dataset,
    diagnose_record,
    diagnose_records,
    load_json_records,
    write_diagnostics,
)

__all__ = [
    "CATEGORY_ORDER",
    "DEFECT_COUNT_BUCKETS",
    "SUMMARY_FIELDS",
    "diagnose",
    "diagnose_files",
    "diagnose_dataset",
    "diagnose_record",
    "diagnose_records",
    "load_json_records",
    "write_diagnostics",
]
