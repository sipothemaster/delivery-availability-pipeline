import os


PROJECT_ID = os.getenv("GCP_PROJECT_ID", "delivery-availability-research")
BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "delivery-availability-research-data-sipo")
DATASET_ID = os.getenv("BQ_DATASET_ID", "delivery_availability")
LOCATION = os.getenv("GCP_LOCATION", "europe-west2")

JOBS_TABLE = os.getenv("BQ_JOBS_TABLE", "scrape_jobs")
SNAPSHOTS_TABLE = os.getenv("BQ_SNAPSHOTS_TABLE", "restaurant_snapshots")
TABLE_SUFFIX = os.getenv("BQ_TABLE_SUFFIX", "")


def table_id(table_name):
    return f"{PROJECT_ID}.{DATASET_ID}.{table_name}{TABLE_SUFFIX}"
