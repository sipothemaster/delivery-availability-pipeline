# Delivery Availability Pipeline

Cloud pipeline for collecting UK delivery availability data, starting with Just Eat.

The production path is:

```text
Cloud Run Job task creator
  -> Cloud Tasks queue
    -> Cloud Run worker
      -> provider API
        -> GCS raw JSON
        -> BigQuery tables
```

## What Is Included

- `cloud_pipeline/`: production Cloud Tasks and Cloud Run pipeline.
- `configs/`: historical Cloud Run Job environment files for full weekday/weekend runs.
- `docs/`: project memory and operational notes.
- `tools/export_full_postcodes.py`: helper for rebuilding the full postcode input CSV.
- `research/justeat_api_reverse/`: scripts that document the Just Eat API reverse-engineering process.
- `research/justeat_menu_reverse/`: scripts and notes for Just Eat menu reverse engineering.

Old grocery scrapers, local sqlite workers, and one-off test outputs are intentionally not included in the production path. The Just Eat browser/API probes are preserved under `research/`.

## Core Files

- `cloud_pipeline/task_worker_service.py`: Flask worker endpoint for Cloud Tasks.
- `cloud_pipeline/justeat_api.py`: Just Eat listing API client and parser.
- `cloud_pipeline/create_tasks_cloud.py`: Cloud Tasks creator.
- `cloud_pipeline/run_task_creator_job.py`: Cloud Run Job entrypoint for task creation.
- `cloud_pipeline/schema.py`: BigQuery schemas.
- `cloud_pipeline/setup_tables.py`: BigQuery table setup.
- `Dockerfile.tasks`: Cloud Run worker image.
- `cloudbuild.tasks.yaml`: Cloud Build config for the worker image.

## Research And Tools

The production pipeline does not import anything under `research/` or `tools/`.
These files are kept for reproducibility and future reverse engineering.

- `research/justeat_api_reverse/Inspect_JustEat_Page.py`
  - inspected embedded page scripts and `__NEXT_DATA__`.
- `research/justeat_api_reverse/Inspect_JustEat_NextData.py`
  - walked `__NEXT_DATA__` to find restaurant-like payloads and API config.
- `research/justeat_api_reverse/Search_JustEat_Chunks.py`
  - searched Next.js bundles for API keywords.
- `research/justeat_api_reverse/Probe_JustEat_API.py`
  - captured browser network responses.
- `research/justeat_api_reverse/Test_JustEat_Internal_API.py`
  - tested API endpoint candidates.
- `research/justeat_api_reverse/Scrape_JustEat.py`
  - older combined browser/API scraper that preceded the production client.
- `research/justeat_api_reverse/Benchmark_JustEat.py`
  - browser loading benchmark retained for historical context.
- `research/justeat_menu_reverse/reverse_justeat_menu.py`
  - menu HTML/CDN extraction tool.
- `tools/export_full_postcodes.py`
  - postcode input reconstruction helper.

Install local research/tool dependencies separately:

```powershell
pip install -r requirements-tools.txt
playwright install chromium
```

## Environment

The pipeline is configured through environment variables:

```text
GCP_PROJECT_ID
GCS_BUCKET_NAME
BQ_DATASET_ID
GCP_LOCATION
BQ_TABLE_SUFFIX
```

Worker rate-limit controls:

```text
ENABLE_GLOBAL_JUSTEAT_RATE_LIMIT=true
JUSTEAT_RATE_LIMIT_SPACING_MS=1300
JUSTEAT_RATE_LIMIT_START_GUARD_MS=1000
JUSTEAT_RATE_LIMIT_KEY=justeat-production-1300ms
ENABLE_JUSTEAT_429_BAN_CIRCUIT=true
JUSTEAT_BAN_KEY=justeat-production-1300ms
JUSTEAT_BAN_SECONDS=3600
```

## Build

```powershell
gcloud.cmd builds submit --config cloudbuild.tasks.yaml --project delivery-availability-research
```

## Deploy Worker

```powershell
gcloud.cmd run deploy delivery-task-worker `
  --image europe-west2-docker.pkg.dev/delivery-availability-research/delivery-pipeline/delivery-task-worker:latest `
  --region europe-west2 `
  --project delivery-availability-research `
  --no-allow-unauthenticated
```

Grant Cloud Tasks caller permission:

```powershell
gcloud.cmd run services add-iam-policy-binding delivery-task-worker `
  --region=europe-west2 `
  --project=delivery-availability-research `
  --member=serviceAccount:scheduler-runner@delivery-availability-research.iam.gserviceaccount.com `
  --role=roles/run.invoker
```

## Create Tables

```powershell
$env:BQ_TABLE_SUFFIX = "_example"
.\.venv\Scripts\python.exe -m cloud_pipeline.setup_tables
```

## Create Tasks

For large runs, use the Cloud Run Job entrypoint:

```powershell
python -m cloud_pipeline.run_task_creator_job
```

Or call the task creator directly:

```powershell
python -m cloud_pipeline.create_tasks_cloud `
  --postcode-file gs://delivery-availability-research-data-sipo/input/postcodes_full.csv `
  --service-url https://YOUR-WORKER-URL `
  --queue-id delivery-scrape-example `
  --windows weekday `
  --daily-start-date 2026-05-19 `
  --daily-days 2 `
  --daily-local-start-time 12:00 `
  --daily-local-end-time 20:00 `
  --run-id weekday-full-YYYYMMDD `
  --skip-existing
```

## Documentation

Start with:

- `docs/PROJECT_CONTEXT.md`
- `docs/WORKLOG.md`
- `docs/NEXT_STEPS.md`
