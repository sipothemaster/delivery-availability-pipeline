# Delivery Availability Pipeline

Research software for collecting and structuring postcode-level food-delivery
availability data. The current implementation targets Just Eat and runs on
Google Cloud using Cloud Run, Cloud Tasks, Cloud Storage, and BigQuery.

This repository publishes the software and research method only. It does not
contain, distribute, or provide access to collected Just Eat data.

## Architecture

```text
Cloud Run task-creator job
  -> Cloud Tasks queue
    -> authenticated Cloud Run worker
      -> public provider endpoint
        -> compressed raw response in private Cloud Storage
        -> operational and parsed records in private BigQuery tables
```

The task creator assigns deterministic jobs to bounded collection windows. The worker applies a shared request-start limiter before contacting the upstream service, records request diagnostics, stores the source response privately, and normalises selected fields for analysis.

## Responsible Collection

The software was designed for bounded academic research rather than unrestricted crawling. Traffic control is deliberately layered:

1. **Workload shaping:** a finite postcode list is deterministically distributed across explicit collection windows.
2. **Cloud Tasks pacing:** scheduled delivery, a bounded dispatch rate, concurrency limits, and retry backoff control traffic entering Cloud Run.
3. **Global hard lock:** all Cloud Run instances reserve provider request-start slots through shared Cloud Storage state. For the established postcode configuration, reserved starts are separated by at least `1300 ms`, even when task delivery, cold starts, database work, and network latency fluctuate.
4. **429 circuit breaker:** one observed HTTP 429 sets shared state that defers new provider requests for `3600 seconds`.

Cloud Tasks is not treated as the final API limiter: it controls delivery to workers, while the global hard lock controls provider request starts. The software also records millisecond-level scheduling, lock, request, status, and latency evidence so these safeguards can be audited after a run.

The project does not use account login, CAPTCHA bypass, proxy rotation, or access-control evasion. Raw responses and derived research data remain private. These controls do not by themselves establish legal permission for every use; operators must review current terms, robots guidance, institutional approvals, and applicable law. See [Responsible data collection](docs/ETHICAL_DATA_COLLECTION.md) for the full design, diagrams, limitations, and pre-run checklist.

## Data Model

The pipeline can produce four analytical entities:

| Entity | Grain | Purpose |
| --- | --- | --- |
| Static coverage | postcode x restaurant | Recorded delivery coverage and selected delivery attributes |
| Restaurant profile | restaurant | Stable restaurant identity, location, cuisine, rating, and platform URL |
| Opening schedule | restaurant x service x day x interval | Normalised delivery or collection opening intervals |
| Observed availability | postcode x window x restaurant | Restaurants observed as open for delivery during a collection window |

Operational manifests, events, diagnostics, and private raw responses support
provenance and reprocessing but are not research data releases. See the
[data model](docs/DATA_MODEL.md) for keys, field semantics, and limitations.

## Repository Layout

- `cloud_pipeline/`: production task creation, worker, parsing, and schemas.
- `configs/`: redacted example task-creator configurations.
- `research/justeat_api_reverse/`: documented listing API discovery process.
- `research/justeat_menu_reverse/`: documented menu-manifest investigation.
- `tools/`: private-data backfill and export utilities; no data are included.
- `docs/`: methods, data model, governance, and project documentation.

Production modules do not import from `research/` or `tools/`.

## Installation

Create a Python environment and install the production dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-cloud.txt
```

Install optional research and export dependencies separately:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-tools.txt
.\.venv\Scripts\playwright.exe install chromium
```

## Configuration

Real cloud identifiers belong in deployment-time environment variables or a
private configuration store, never in tracked files.

Required deployment settings include:

```text
GCP_PROJECT_ID
GCS_BUCKET_NAME
BQ_DATASET_ID
GCP_LOCATION
POSTCODE_FILE
TASK_WORKER_SERVICE_URL
TASK_QUEUE_ID
OIDC_SERVICE_ACCOUNT_EMAIL
```

Recommended postcode endpoint safety settings are:

```text
ENABLE_GLOBAL_JUSTEAT_RATE_LIMIT=true
JUSTEAT_RATE_LIMIT_SPACING_MS=1300
JUSTEAT_RATE_LIMIT_START_GUARD_MS=1000
ENABLE_JUSTEAT_429_BAN_CIRCUIT=true
JUSTEAT_BAN_SECONDS=3600
```

Limiter and ban keys should be deployment-specific and must not contain secrets.
Do not treat these historical research settings as permission to collect from
the service or as a guaranteed universally safe request rate.

## Build And Deploy

Build the worker image with redacted substitutions:

```powershell
gcloud builds submit `
  --config cloudbuild.tasks.yaml `
  --project YOUR_GCP_PROJECT_ID `
  --substitutions _REGION=europe-west2,_ARTIFACT_REPOSITORY=YOUR_REPOSITORY,_IMAGE_NAME=YOUR_IMAGE,_IMAGE_TAG=latest
```

Deployment requires a private environment configuration based on the examples
under `configs/`. The Cloud Run worker should require authentication, and the
task-calling service account should receive only the minimum invocation and data
permissions needed for the selected workflow.

No deployment command in this repository creates a complete production
environment automatically. Review IAM, storage retention, budget controls,
queue limits, and upstream collection approval before running it.

## Reproducibility

The reverse-engineering history is retained because the production API client
was derived through browser network inspection and controlled endpoint probes.
The process and the transition from browser automation to direct structured
requests are described in [Methods](docs/METHODS.md).

Generated CSV, Parquet, JSON, logs, credentials, browser profiles, and cloud
configuration values are excluded from version control. The repository contains
no public data release; see [Data availability](docs/DATA_AVAILABILITY.md).

## Citation

Citation metadata are provided in [`CITATION.cff`](CITATION.cff). GitHub can
render these metadata through its **Cite this repository** interface. Cite the
specific released software version used in an analysis. A Zenodo DOI will be
added to the citation file after the first archived release. The release process
is documented in [Releasing and citation](docs/RELEASING.md).

## Contributing And Security

See [CONTRIBUTING.md](CONTRIBUTING.md) before proposing changes. Report security
issues according to [SECURITY.md](SECURITY.md) and do not open public issues that
contain credentials, signed URLs, private data, or cloud resource identifiers.

## License

The software is released under the [MIT License](LICENSE). This license covers
the source code in this repository, not third-party website content or data
collected with the software.
