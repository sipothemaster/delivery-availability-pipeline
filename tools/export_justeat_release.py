"""Export the four stakeholder-facing Just Eat tables as sharded Parquet.

The exporter reads tables through the BigQuery Storage Read API. It does not
run SQL, modify production tables, or include raw JSON and operational logs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from google.cloud import bigquery
from google.cloud import bigquery_storage_v1


DEFAULT_PROJECT_ID = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
DEFAULT_DATASET_ID = os.getenv("BQ_DATASET_ID", "delivery_availability")
DEFAULT_LOCATION = os.getenv("GCP_LOCATION", "europe-west2")
DEFAULT_RELEASE_DIR = Path("data/output/justeat_data_release")


@dataclass(frozen=True)
class ExportSpec:
    key: str
    source_table: str
    output_dir: str
    description: str
    partition_field: str | None = None
    included_partitions: tuple[str, ...] | None = None


EXPORT_SPECS = {
    "profile": ExportSpec(
        key="profile",
        source_table=os.getenv("BQ_PROFILE_TABLE", "restaurant_profile"),
        output_dir="restaurant_profile",
        description="One row per unique Just Eat restaurant.",
    ),
    "opening_times": ExportSpec(
        key="opening_times",
        source_table=os.getenv("BQ_OPENING_TIMES_TABLE", "restaurant_opening_times"),
        output_dir="restaurant_opening_times",
        description=(
            "Normalised restaurant opening intervals by service, day, and time."
        ),
    ),
    "static_coverage": ExportSpec(
        key="static_coverage",
        source_table=os.getenv(
            "BQ_STATIC_COVERAGE_TABLE",
            "postcode_restaurant_delivery_map",
        ),
        output_dir="static_coverage",
        description="Static postcode-to-restaurant delivery coverage map.",
    ),
    "observed_availability": ExportSpec(
        key="observed_availability",
        source_table=os.getenv(
            "BQ_OBSERVED_AVAILABILITY_TABLE",
            "restaurant_snapshots",
        ),
        output_dir="observed_availability",
        description="Observed open-now availability in four collection windows.",
        partition_field="planned_window",
        included_partitions=(
            "weekday_afternoon",
            "weekday_evening",
            "weekday_early_hours",
            "saturday_peak",
        ),
    ),
}


def safe_partition(value: object) -> str:
    text = "null" if value is None else str(value)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")
    return text or "empty"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class ShardedParquetWriter:
    directory: Path
    schema: pa.Schema
    rows_per_file: int
    partition_label: str = ""
    file_index: int = 0
    rows_in_file: int = 0
    rows_total: int = 0
    writer: pq.ParquetWriter | None = None
    current_path: Path | None = None
    files: list[dict[str, object]] = field(default_factory=list)

    def _open(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.current_path = self.directory / f"part-{self.file_index:05d}.parquet"
        self.writer = pq.ParquetWriter(
            self.current_path,
            self.schema,
            compression="snappy",
            use_dictionary=True,
            write_statistics=True,
            version="2.6",
        )
        self.rows_in_file = 0

    def _close_current(self) -> None:
        if self.writer is None or self.current_path is None:
            return
        self.writer.close()
        metadata = pq.ParquetFile(self.current_path).metadata
        self.files.append(
            {
                "path": self.current_path,
                "partition": self.partition_label,
                "rows": metadata.num_rows,
            }
        )
        self.writer = None
        self.current_path = None
        self.file_index += 1
        self.rows_in_file = 0

    def write(self, table: pa.Table) -> None:
        offset = 0
        while offset < table.num_rows:
            if self.writer is None:
                self._open()
            capacity = self.rows_per_file - self.rows_in_file
            chunk = table.slice(offset, min(capacity, table.num_rows - offset))
            self.writer.write_table(chunk)
            written = chunk.num_rows
            self.rows_in_file += written
            self.rows_total += written
            offset += written
            if self.rows_in_file >= self.rows_per_file:
                self._close_current()

    def close(self) -> None:
        self._close_current()


class SampleCollector:
    def __init__(self, limit: int):
        self.limit = limit
        self.tables: list[pa.Table] = []
        self.rows = 0

    def add(self, table: pa.Table) -> None:
        if self.rows >= self.limit:
            return
        chunk = table.slice(0, min(self.limit - self.rows, table.num_rows))
        if chunk.num_rows:
            self.tables.append(chunk)
            self.rows += chunk.num_rows

    def write_csv(self, path: Path) -> None:
        if not self.tables:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        pa.concat_tables(self.tables).to_pandas().to_csv(
            path,
            index=False,
            encoding="utf-8-sig",
        )


def ensure_empty_release_dir(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise RuntimeError(f"Release directory is not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def export_spec(
    spec: ExportSpec,
    release_dir: Path,
    bq_client: bigquery.Client,
    read_client: bigquery_storage_v1.BigQueryReadClient,
    project_id: str,
    dataset_id: str,
    rows_per_file: int,
    sample_rows: int,
    max_rows: int | None,
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    table_id = f"{project_id}.{dataset_id}.{spec.source_table}"
    source = bq_client.get_table(table_id)
    iterator = bq_client.list_rows(
        source,
        selected_fields=source.schema,
        max_results=max_rows,
    )
    output_root = release_dir / spec.output_dir
    writers: dict[str, ShardedParquetWriter] = {}
    samples: dict[str, SampleCollector] = {}
    exported_rows = 0
    processed_rows = 0
    excluded_rows = 0
    next_progress = 1_000_000

    for batch in iterator.to_arrow_iterable(
        bqstorage_client=read_client,
        max_stream_count=1,
    ):
        arrow_table = pa.Table.from_batches([batch])
        processed_rows += arrow_table.num_rows
        if spec.partition_field:
            field_values = arrow_table.column(spec.partition_field)
            partitions = pc.unique(field_values).to_pylist()
            for value in partitions:
                mask = pc.is_null(field_values) if value is None else pc.equal(
                    field_values, value
                )
                partition_table = arrow_table.filter(mask)
                label = safe_partition(value)
                if (
                    spec.included_partitions is not None
                    and label not in spec.included_partitions
                ):
                    excluded_rows += partition_table.num_rows
                    continue
                if label not in writers:
                    partition_dir = output_root / f"{spec.partition_field}={label}"
                    writers[label] = ShardedParquetWriter(
                        partition_dir,
                        partition_table.schema,
                        rows_per_file,
                        partition_label=label,
                    )
                    samples[label] = SampleCollector(sample_rows)
                writers[label].write(partition_table)
                samples[label].add(partition_table)
                exported_rows += partition_table.num_rows
        else:
            label = "all"
            if label not in writers:
                writers[label] = ShardedParquetWriter(
                    output_root,
                    arrow_table.schema,
                    rows_per_file,
                )
                samples[label] = SampleCollector(sample_rows)
            writers[label].write(arrow_table)
            samples[label].add(arrow_table)
            exported_rows += arrow_table.num_rows

        if processed_rows >= next_progress:
            print(
                f"{spec.key}: {processed_rows:,} source rows, "
                f"{exported_rows:,} exported",
                flush=True,
            )
            next_progress += 1_000_000

    file_rows: list[dict[str, object]] = []
    for writer in writers.values():
        writer.close()
        for file_row in writer.files:
            file_row["dataset"] = spec.output_dir
            file_rows.append(file_row)

    sample_root = release_dir / "csv_samples"
    for label, sample in samples.items():
        suffix = "" if label == "all" else f"__{label}"
        sample.write_csv(sample_root / f"{spec.output_dir}{suffix}__sample.csv")

    dictionary_rows = [
        {
            "dataset": spec.output_dir,
            "source_table": spec.source_table,
            "field_name": field.name,
            "field_type": field.field_type,
            "mode": field.mode,
            "description": field.description or "",
        }
        for field in source.schema
    ]
    expected_rows = int(source.num_rows)
    if max_rows is None and processed_rows != expected_rows:
        raise RuntimeError(
            f"Row-count mismatch for {spec.source_table}: "
            f"expected {expected_rows:,}, processed {processed_rows:,}"
        )

    summary = {
        "dataset": spec.output_dir,
        "source_table": spec.source_table,
        "description": spec.description,
        "source_rows": expected_rows,
        "exported_rows": exported_rows,
        "excluded_rows": excluded_rows,
        "parquet_files": len(file_rows),
        "partition_field": spec.partition_field or "",
        "is_sample_export": max_rows is not None,
    }
    print(
        f"{spec.key}: complete, {exported_rows:,} rows, {len(file_rows)} files",
        flush=True,
    )
    return summary, file_rows, dictionary_rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def validate_and_enrich_files(
    release_dir: Path,
    file_rows: list[dict[str, object]],
) -> None:
    for row in file_rows:
        path = Path(row.pop("path"))
        metadata = pq.ParquetFile(path).metadata
        if metadata.num_rows != row["rows"]:
            raise RuntimeError(f"Parquet metadata mismatch: {path}")
        row["relative_path"] = path.relative_to(release_dir).as_posix()
        row["bytes"] = path.stat().st_size
        row["sha256"] = sha256_file(path)


def partition_manifest(file_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for row in file_rows:
        key = (str(row["dataset"]), str(row["partition"] or "all"))
        if key not in grouped:
            grouped[key] = {
                "dataset": key[0],
                "partition": key[1],
                "rows": 0,
                "parquet_files": 0,
                "bytes": 0,
            }
        grouped[key]["rows"] = int(grouped[key]["rows"]) + int(row["rows"])
        grouped[key]["parquet_files"] = int(
            grouped[key]["parquet_files"]
        ) + 1
        grouped[key]["bytes"] = int(grouped[key]["bytes"]) + int(row["bytes"])
    return [grouped[key] for key in sorted(grouped)]


def write_readme(
    release_dir: Path,
    summaries: list[dict[str, object]],
    generated_at: str,
) -> None:
    rows = "\n".join(
        f"| `{item['dataset']}` | {int(item['exported_rows']):,} | "
        f"{item['parquet_files']} | `{item['source_table']}` |"
        for item in summaries
    )
    content = f"""# Just Eat Data Release

Generated: {generated_at}

This package contains the four requested Just Eat research datasets. It excludes
direct grocery-provider availability, raw API JSON, operational logs, Cloud
Tasks metadata, credentials, and service configuration.

## Contents

| Dataset | Rows | Parquet files | BigQuery source |
| --- | ---: | ---: | --- |
{rows}

`static_coverage` is the postcode-to-restaurant coverage map reconstructed from
the selected private source responses. Its `is_delivery` field records the
source response state and should be retained for analysis.

`observed_availability` contains open-now observations from the selected
temporal table. It is partitioned by `planned_window`: `weekday_afternoon`,
`weekday_evening`, `weekday_early_hours`, and `saturday_peak`. Each postcode was
assigned once to each window; multiple collection dates were dispatch shards,
not repeated national observations. Rows labelled `smoke_temporal` are excluded
because they belong to the small pipeline validation run, not a collection
window.

`restaurant_opening_times` is normalised to one row per restaurant, service,
day, and opening interval. A restaurant can therefore have multiple rows for a
single day. `crosses_midnight` identifies overnight intervals.

## Supporting Files

- `manifest.csv`: dataset-level source and row-count information.
- `partition_manifest.csv`: row counts and sizes by dataset/window partition.
- `file_manifest.csv`: file sizes, row counts, partitions, and SHA-256 hashes.
- `data_dictionary.csv`: source field names and BigQuery types.
- `csv_samples/`: small UTF-8 CSV samples for inspection only.

## Reading Parquet

Python:

```python
import pandas as pd

profile = pd.read_parquet("restaurant_profile")
```

DuckDB can query all shards without combining them:

```sql
SELECT *
FROM read_parquet('static_coverage/*.parquet');
```

The Parquet files are the canonical full exports. CSV samples are not complete
datasets.
"""
    (release_dir / "README.md").write_text(content, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--dataset-id", default=DEFAULT_DATASET_ID)
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--release-dir", type=Path, default=DEFAULT_RELEASE_DIR)
    parser.add_argument(
        "--table",
        action="append",
        choices=[*EXPORT_SPECS, "all"],
        default=None,
        help="Dataset to export; repeat for multiple datasets. Defaults to all.",
    )
    parser.add_argument("--rows-per-file", type=int, default=1_000_000)
    parser.add_argument("--sample-rows", type=int, default=1_000)
    parser.add_argument(
        "--max-rows",
        type=int,
        help="Smoke-test limit applied independently to each selected table.",
    )
    args = parser.parse_args()
    if not args.project_id:
        parser.error("Set GCP_PROJECT_ID or pass --project-id.")
    return args


def main() -> None:
    args = parse_args()
    selected = args.table or ["all"]
    if "all" in selected:
        selected = list(EXPORT_SPECS)
    selected = list(dict.fromkeys(selected))

    ensure_empty_release_dir(args.release_dir)
    generated_at = datetime.now(timezone.utc).isoformat()
    bq_client = bigquery.Client(project=args.project_id, location=args.location)
    read_client = bigquery_storage_v1.BigQueryReadClient()

    summaries: list[dict[str, object]] = []
    file_rows: list[dict[str, object]] = []
    dictionary_rows: list[dict[str, object]] = []
    for key in selected:
        summary, files, fields = export_spec(
            EXPORT_SPECS[key],
            args.release_dir,
            bq_client,
            read_client,
            args.project_id,
            args.dataset_id,
            args.rows_per_file,
            args.sample_rows,
            args.max_rows,
        )
        summaries.append(summary)
        file_rows.extend(files)
        dictionary_rows.extend(fields)

    validate_and_enrich_files(args.release_dir, file_rows)
    write_csv(args.release_dir / "manifest.csv", summaries)
    write_csv(
        args.release_dir / "partition_manifest.csv",
        partition_manifest(file_rows),
    )
    write_csv(args.release_dir / "file_manifest.csv", file_rows)
    write_csv(args.release_dir / "data_dictionary.csv", dictionary_rows)
    write_readme(args.release_dir, summaries, generated_at)

    total_bytes = sum(int(row["bytes"]) for row in file_rows)
    print(f"Release directory: {args.release_dir.resolve()}")
    print(f"Parquet size: {total_bytes / (1024 ** 3):.3f} GiB")


if __name__ == "__main__":
    main()
