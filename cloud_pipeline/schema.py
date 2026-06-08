from google.cloud import bigquery


SCRAPE_JOBS_SCHEMA = [
    bigquery.SchemaField("job_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("provider", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("postcode", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("planned_window", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("scheduled_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("attempt_count", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("max_attempts", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("started_at", "TIMESTAMP"),
    bigquery.SchemaField("finished_at", "TIMESTAMP"),
    bigquery.SchemaField("raw_uri", "STRING"),
    bigquery.SchemaField("processed_rows", "INTEGER"),
    bigquery.SchemaField("error", "STRING"),
]


RESTAURANT_SNAPSHOTS_SCHEMA = [
    bigquery.SchemaField("snapshot_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("job_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("provider", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("postcode", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("planned_window", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("scheduled_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("captured_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("Name", "STRING"),
    bigquery.SchemaField("Rating", "FLOAT"),
    bigquery.SchemaField("ReviewCount", "INTEGER"),
    bigquery.SchemaField("Categories", "STRING"),
    bigquery.SchemaField("DeliveryTime", "STRING"),
    bigquery.SchemaField("DeliveryFee", "STRING"),
    bigquery.SchemaField("MinimumOrder", "STRING"),
    bigquery.SchemaField("Offer", "STRING"),
    bigquery.SchemaField("Tags", "STRING"),
    bigquery.SchemaField("Url", "STRING"),
    bigquery.SchemaField("Vertical", "STRING"),
    bigquery.SchemaField("JustEatId", "STRING"),
    bigquery.SchemaField("CuisineIds", "STRING"),
]


JOB_MANIFEST_SCHEMA = [
    bigquery.SchemaField("job_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("run_id", "STRING"),
    bigquery.SchemaField("provider", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("postcode", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("planned_window", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("scheduled_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("task_name", "STRING"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField("attempt_count", "INTEGER"),
    bigquery.SchemaField("max_attempts", "INTEGER"),
    bigquery.SchemaField("started_at", "TIMESTAMP"),
    bigquery.SchemaField("finished_at", "TIMESTAMP"),
    bigquery.SchemaField("raw_uri", "STRING"),
    bigquery.SchemaField("processed_rows", "INTEGER"),
    bigquery.SchemaField("error", "STRING"),
]


JOB_EVENTS_SCHEMA = [
    bigquery.SchemaField("event_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("job_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("provider", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("postcode", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("planned_window", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("event_type", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("event_time", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("message", "STRING"),
    bigquery.SchemaField("raw_uri", "STRING"),
    bigquery.SchemaField("processed_rows", "INTEGER"),
]


JOB_DIAGNOSTICS_SCHEMA = [
    bigquery.SchemaField("diagnostic_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("job_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("run_id", "STRING"),
    bigquery.SchemaField("provider", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("postcode", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("planned_window", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("diagnostic_time", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("worker_received_at", "TIMESTAMP"),
    bigquery.SchemaField("worker_started_at", "TIMESTAMP"),
    bigquery.SchemaField("limiter_enabled", "BOOLEAN"),
    bigquery.SchemaField("limiter_key", "STRING"),
    bigquery.SchemaField("limiter_acquire_started_at", "TIMESTAMP"),
    bigquery.SchemaField("limiter_acquire_finished_at", "TIMESTAMP"),
    bigquery.SchemaField("limiter_attempts", "INTEGER"),
    bigquery.SchemaField("limiter_spacing_ms", "INTEGER"),
    bigquery.SchemaField("limiter_previous_next_allowed_ms", "INTEGER"),
    bigquery.SchemaField("limiter_token_reserved_for_ms", "INTEGER"),
    bigquery.SchemaField("limiter_token_reserved_for", "TIMESTAMP"),
    bigquery.SchemaField("limiter_wait_ms", "INTEGER"),
    bigquery.SchemaField("api_request_started_at", "TIMESTAMP"),
    bigquery.SchemaField("api_request_finished_at", "TIMESTAMP"),
    bigquery.SchemaField("api_latency_ms", "INTEGER"),
    bigquery.SchemaField("api_url", "STRING"),
    bigquery.SchemaField("http_status", "INTEGER"),
    bigquery.SchemaField("outcome", "STRING"),
    bigquery.SchemaField("processed_rows", "INTEGER"),
    bigquery.SchemaField("raw_uri", "STRING"),
    bigquery.SchemaField("error", "STRING"),
]
