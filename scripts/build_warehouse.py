#!/usr/bin/env python3
"""Build the local warehouse that mirrors the QualityControl Power BI model.

This is the other half of the cross-platform reconciliation demo. The same QC
metrics are defined again as SQL views, the way a data engineering team would
implement them in Snowflake or Databricks -- independently of whoever built the
Power BI model, which is exactly how definitions drift apart in real
organisations.

Two of the views diverge on purpose, and both divergences are ones that actually
happen:

  * ``oos_rate`` divides by the number of *batches* rather than the number of
    *tests*. Same name, same intent, materially different number.
  * ``batch_yield_pct`` averages the per-batch ratio instead of dividing the
    summed yields -- an average of ratios, not a ratio of sums, which is a
    classic and easily-missed error.

The rest agree, so the report has to distinguish real divergence from noise
rather than flagging everything.

DuckDB is used because it implements the standard information schema that
Snowflake and Databricks also expose, so the extraction path being exercised is
the real one -- only the connection differs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

DATABASE = Path("data/warehouse/quality_control.duckdb")

SCHEMA = """
CREATE TABLE batch (
    batch_id           INTEGER,
    batch_number       VARCHAR,
    product_id         INTEGER,
    site_id            INTEGER,
    manufacture_date   DATE,
    release_date       DATE,
    theoretical_yield  INTEGER,
    actual_yield       INTEGER,
    status             VARCHAR,
    right_first_time   BOOLEAN
);

CREATE TABLE test_result (
    test_result_id     INTEGER,
    batch_id           INTEGER,
    test_type          VARCHAR,
    test_date          DATE,
    result_value       DOUBLE,
    specification_min  DOUBLE,
    specification_max  DOUBLE,
    result_status      VARCHAR,
    instrument         VARCHAR,
    calibration_current BOOLEAN
);

CREATE TABLE product (
    product_id   INTEGER,
    product_name VARCHAR,
    dosage_form  VARCHAR,
    strength     VARCHAR,
    atc_code     VARCHAR
);

CREATE TABLE site (
    site_id            INTEGER,
    site_name          VARCHAR,
    country            VARCHAR,
    region             VARCHAR,
    gmp_licence_number VARCHAR
);
"""

VIEWS = {
    # DIVERGENT: the Power BI measure divides OOS results by tests performed.
    # This divides by batches. Same name, different denominator, different number.
    "oos_rate": """
        SELECT
            CAST(COUNT(*) FILTER (WHERE tr.result_status = 'Failed') AS DOUBLE)
            / NULLIF((SELECT COUNT(*) FROM batch), 0) AS oos_rate
        FROM test_result tr
    """,
    # DIVERGENT: an average of per-batch ratios, where Power BI takes the ratio
    # of summed yields. These agree only when every batch is the same size.
    "batch_yield_pct": """
        SELECT AVG(CAST(b.actual_yield AS DOUBLE) / NULLIF(b.theoretical_yield, 0))
                   AS batch_yield_pct
        FROM batch b
    """,
    # The rest are faithful re-implementations and should read as consistent.
    "batches_manufactured": "SELECT COUNT(*) AS batches_manufactured FROM batch",
    "batches_rejected": """
        SELECT COUNT(*) AS batches_rejected FROM batch WHERE status = 'Rejected'
    """,
    "tests_performed": "SELECT COUNT(*) AS tests_performed FROM test_result",
    "oos_results": """
        SELECT COUNT(*) AS oos_results FROM test_result WHERE result_status = 'Failed'
    """,
    # Defined only in the warehouse -- reported as unique, not as a conflict.
    "instrument_utilisation": """
        SELECT instrument, COUNT(*) AS tests
        FROM test_result
        GROUP BY instrument
    """,
}


def build(path: Path = DATABASE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    connection = duckdb.connect(str(path))
    try:
        connection.execute(SCHEMA)
        for name, definition in VIEWS.items():
            connection.execute(f"CREATE VIEW {name} AS {definition}")
        connection.commit()
    finally:
        connection.close()
    return path


def main() -> int:
    path = build()
    connection = duckdb.connect(str(path), read_only=True)
    try:
        tables = connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchone()[0]
        views = connection.execute(
            "SELECT COUNT(*) FROM information_schema.views WHERE table_schema = 'main'"
        ).fetchone()[0]
    finally:
        connection.close()

    print(f"built {path}")
    print(f"  {tables} tables, {views} views")
    return 0


if __name__ == "__main__":
    sys.exit(main())
