import argparse

from google.cloud import bigquery

from cloud_pipeline import config
from cloud_pipeline.schema import (
    JOB_DIAGNOSTICS_SCHEMA,
    JOB_EVENTS_SCHEMA,
    JOB_MANIFEST_SCHEMA,
    RESTAURANT_SNAPSHOTS_SCHEMA,
    SCRAPE_JOBS_SCHEMA,
)


def ensure_dataset(client):
    dataset = bigquery.Dataset(f"{config.PROJECT_ID}.{config.DATASET_ID}")
    dataset.location = config.LOCATION
    return client.create_dataset(dataset, exists_ok=True)


def ensure_table(client, table_name, schema, partition_field=None):
    table = bigquery.Table(config.table_id(table_name), schema=schema)
    if partition_field:
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field=partition_field,
        )
    created = client.create_table(table, exists_ok=True)
    existing_fields = {field.name for field in created.schema}
    missing_fields = [field for field in schema if field.name not in existing_fields]
    if missing_fields:
        created.schema = list(created.schema) + missing_fields
        created = client.update_table(created, ["schema"])
    return created


def parse_args():
    parser = argparse.ArgumentParser(description="Create BigQuery tables for cloud tests.")
    parser.add_argument("--project-id", default=config.PROJECT_ID)
    return parser.parse_args()


def main():
    args = parse_args()
    client = bigquery.Client(project=args.project_id, location=config.LOCATION)
    ensure_dataset(client)
    ensure_table(client, config.JOBS_TABLE, SCRAPE_JOBS_SCHEMA, partition_field="scheduled_at")
    ensure_table(client, "job_manifest", JOB_MANIFEST_SCHEMA, partition_field="scheduled_at")
    ensure_table(client, "job_events", JOB_EVENTS_SCHEMA, partition_field="event_time")
    ensure_table(
        client,
        "job_diagnostics",
        JOB_DIAGNOSTICS_SCHEMA,
        partition_field="diagnostic_time",
    )
    ensure_table(
        client,
        config.SNAPSHOTS_TABLE,
        RESTAURANT_SNAPSHOTS_SCHEMA,
        partition_field="captured_at",
    )
    print(f"Dataset ready: {config.PROJECT_ID}.{config.DATASET_ID}")
    print(f"Table ready: {config.table_id(config.JOBS_TABLE)}")
    print(f"Table ready: {config.table_id(config.SNAPSHOTS_TABLE)}")


if __name__ == "__main__":
    main()
