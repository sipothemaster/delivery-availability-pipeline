# AGENTS.md

## Purpose

This repository contains the Just Eat delivery-availability data pipeline. Its
production path runs on GCP and collects postcode-level restaurant availability,
raw API responses, restaurant profiles, opening times, and temporal snapshots.

Treat this file as the repository-level operating guide. Read it before editing
or operating the pipeline. More specific instructions may be added in a nested
`AGENTS.md`; a nested file takes precedence for files below its directory.

## Start Here

Before substantial work, read:

1. `README.md`
2. `docs/PROJECT_CONTEXT.md`
3. `docs/WORKLOG.md`
4. `docs/NEXT_STEPS.md`

These documents contain historical run details. Verify mutable cloud state with
read-only GCP queries before reporting that a queue, job, deployment, or run is
currently active or complete.

## Repository Boundaries

- `cloud_pipeline/` is production code.
- `configs/` contains explicit and historical run configurations.
- `tools/` contains offline/backfill/export utilities. Production services must
  not import from it.
- `research/` preserves the Just Eat API and menu reverse-engineering process.
  Production services must not import from it.
- `data/` contains local inputs and generated outputs. Do not commit large,
  generated, or sensitive data unless explicitly requested.
- `docs/` is the durable handoff memory for future work sessions.

The sibling repository `../WebscrapingDeliveryAvailability` is the grocery
retailer integration project. It is a separate Git repository with separate
instructions and dependencies. Do not move its retailer-specific scrapers into
this repository or make either repository import the other. Exchange data only
through documented CSV, GCS, or BigQuery interfaces unless the user explicitly
requests a broader integration.

## Production Architecture

The postcode snapshot flow is:

```text
Cloud Run Job task creator
  -> Cloud Tasks queue
    -> authenticated Cloud Run Flask worker
      -> Just Eat postcode API
        -> compressed raw JSON in GCS
        -> events, diagnostics, and parsed snapshots in BigQuery
```

The menu-manifest flow is separate:

```text
Cloud Run Job manifest task creator
  -> dedicated Cloud Tasks queue
    -> Cloud Run worker
      -> Just Eat menu CDN manifest JSON
        -> manifest results in BigQuery
        -> normalized restaurant opening-time rows in BigQuery
```

Core ownership:

- `cloud_pipeline/create_tasks_cloud.py`: creates deterministic postcode tasks
  and schedules each tag across explicit intervals.
- `cloud_pipeline/run_task_creator_job.py`: environment-driven Cloud Run Job
  entrypoint for postcode task creation.
- `cloud_pipeline/task_worker_service.py`: Flask endpoints, global limiter,
  429 circuit, GCS raw upload, and BigQuery writes.
- `cloud_pipeline/justeat_api.py`: Just Eat postcode API request and parser.
- `cloud_pipeline/create_menu_manifest_tasks.py`: creates restaurant manifest
  tasks from unique restaurant profiles.
- `cloud_pipeline/run_menu_manifest_task_creator_job.py`: manifest creator job
  entrypoint.
- `cloud_pipeline/schema.py` and `cloud_pipeline/setup_tables.py`: BigQuery
  schemas and table provisioning.
- `cloud_pipeline/config.py`: shared GCP resource configuration.

Default cloud resources documented by this project:

- GCP project: `delivery-availability-research`
- region/location: `europe-west2`
- GCS bucket: `delivery-availability-research-data-sipo`
- BigQuery dataset: `delivery_availability`
- Artifact Registry repository: `delivery-pipeline`

Do not assume names, deployed revisions, queue settings, or row counts remain
current solely because they appear in a dated document.

## Rate-Limit Safety

Avoiding HTTP 429 is a production requirement, not merely a retry concern.
Observed postcode API 429 episodes were followed by an approximately one-hour
block of the Cloud Run egress IP.

For the established postcode snapshot configuration:

- Cloud Tasks dispatch rate: `1/s`
- Cloud Tasks maximum concurrency: `4`
- worker global Just Eat spacing: `1300ms`
- worker start guard: `1000ms`
- 429 ban circuit: `3600s`

The worker-level lock is the authoritative request-start limiter across Cloud
Run instances. Queue rate and concurrency control delivery to the worker but do
not replace the global lock. Keep limiter keys unique between independent test
and production pipelines when their traffic must not share state.

Do not reduce postcode API spacing below `1300ms`, raise queue throughput, or
change concurrency as an incidental edit. A prior `1100ms` controlled test
triggered a 429 after 77 successful requests. Any new rate must be tested in an
isolated queue on a bounded sample, with millisecond request diagnostics and
live monitoring, before production use.

On 403, 429, or a repeated upstream failure:

- preserve detailed diagnostics;
- do not add aggressive retries;
- respect the 429 circuit and stop new upstream requests for the configured ban
  period;
- determine whether the response came from the postcode API, page host, or menu
  CDN before changing shared controls.

The menu CDN is a different upstream surface. Its manifest pipeline passed
bounded tests at a `1000ms` hard lock, but this does not justify changing the
postcode API limiter or running both pipelines without considering aggregate
traffic and cloud egress behavior.

## Scheduling And Window Semantics

Each postcode should be assigned once per observation tag. Multiple dates in an
explicit window configuration are capacity shards for that tag, not requests to
observe every postcode on every listed date. Do not accidentally multiply one
observation into one row per date.

Current canonical temporal tags are:

- `weekday_afternoon`
- `weekday_evening`
- `weekday_early_hours`
- `saturday_peak`

Use `full_coverage` for the static/full-coverage source label in downstream
exports. Keep `planned_window` as the snapshot tag field.

Scheduling rules:

- distribute each tag's postcode set deterministically across its configured
  intervals;
- preserve enough capacity margin for queue and worker latency;
- do not let one window's tasks spill into another observation window;
- a worker must validate whether a task is still valid for its intended window
  when the configuration requires a hard boundary;
- use stable task/job identifiers and `--skip-existing` semantics to make task
  creation resumable and avoid duplicates;
- do not enqueue an entire full run locally when the Cloud Run task-creator job
  exists for that purpose.

When changing a dated schedule, create a new run id, queue/resource names, table
suffix, limiter key, and explicit configuration where isolation is required. Do
not repurpose a historical production queue or table unless explicitly asked.

## Data Semantics

### Raw JSON

Successful postcode tasks upload the complete API response as compressed raw
JSON in GCS. BigQuery event rows store the associated `raw_uri`. Raw JSON is the
reprocessable source for fields that were not retained by the snapshot parser.
Do not claim a field was unavailable until checking the raw response.

### Temporal And Full Snapshot Tables

`restaurant_snapshots_*` tables contain restaurants passing the open-delivery
parser:

```text
isDelivery = true
isOpenNowForDelivery = true
isTemporarilyOffline = false
```

These tables answer who appeared deliverable at capture time. They are not the
complete postcode-to-restaurant coverage universe.

The core operational tables use suffixes to isolate runs and generally include:

- `job_manifest_*`
- `job_events_*`
- `job_diagnostics_*`
- `scrape_jobs_*`
- `restaurant_snapshots_*`

Use events and diagnostics as execution truth when a historical manifest status
was not updated correctly.

### Static Postcode-Restaurant Map

`postcode_restaurant_delivery_map` is backfilled from saved raw postcode JSON.
Its intended grain is one postcode/restaurant pairing and it retains the API's
static-coverage `is_delivery` value plus selected delivery fee/time fields.

Do not interpret `is_delivery=false` as proof that a restaurant never delivers.
It represents the response state/coverage recorded by the source run and can be
affected by temporary availability. Preserve the field so analyses can choose
their own filter.

### Restaurant Profile

`restaurant_profile` is one row per restaurant id and contains stable identity
and profile fields such as restaurant name, unique slug/name, generated Just Eat
URL, cuisines, rating, address, and location where available. New raw responses
should not create duplicate restaurant ids.

Do not add menu-manifest state or opening-time arrays to `restaurant_profile`.
Keep the profile table focused on restaurant identity and reusable attributes.

### Opening Times

`restaurant_opening_times` is normalized to one row per:

```text
restaurant_id + service_type + day_of_week + opening interval
```

A restaurant with two service periods on one day therefore has two rows for
that day. Preserve `crosses_midnight`; do not force an overnight interval into
the wrong calendar day. Missing parsed opening times are not equivalent to
`is_delivery=false`.

### Menu Manifests

The preferred direct CDN manifest path is:

```text
https://menu-globalmenucdn.je-apis.com/{restaurant_unique_name}_uk_manifest.json
```

The fallback is:

```text
https://menu-globalmenucdn.je-apis.com/v2_2/{restaurant_unique_name}_uk_manifest.json
```

Record whether `original` or `v2_2` supplied the result. Avoid restaurant HTML
unless CDN/API routes cannot provide the required field. `items.json` is a later
menu-items layer; do not fetch `itemDetails.json` or modifier groups unless an
analysis explicitly needs them.

## Grocery Availability Export

`tools/export_grocery_availability.py` produces the grouped stakeholder CSV from
the static map/profile tables and temporal snapshots. Its output is an analysis
artifact, not a new production crawl.

The export intentionally keeps postcode-geography mappings at their source
grain because a postcode can map to more than one supplied geography row. Do
not silently deduplicate away LSOA/DZ assignments.

The grocery measures are overlapping lenses, not additive categories:

- selected `big_brand` retailers;
- broader `general_grocery` name/cuisine classification;
- official Just Eat `Groceries` cuisine tag.

Do not sum these counts. Brand detection rules and output definitions belong in
`docs/GROCERY_AVAILABILITY_EXPORT.md` and should be updated together with the
exporter.

The historical postcode API raw JSON cannot reproduce the exact Just Eat web
page result for `?vertical=groceries`. The page's `vertical` selection is a
frontend/discovery filter and may use additional Next.js/backend state. Label
the cuisine-tag metric accurately; do not call it exact vertical-page parity.

Postcodes absent from a current Code-Point file are not automatically invalid or
zero-coverage postcodes. Keep geography quality audits separate from Just Eat
availability measures.

## Change Workflow

For production code changes:

1. Inspect the relevant implementation, configuration, schemas, and latest
   handoff documents.
2. Preserve existing run isolation, idempotency, data grain, and rate controls.
3. Make the smallest coherent change.
4. Run local syntax and focused parser/unit checks.
5. Use a tiny local or read-only fixture test where possible.
6. If live behavior must be verified, run a bounded sample in an isolated cloud
   queue/table only after the user authorizes cloud writes.
7. Monitor queue depth, event outcomes, diagnostics, upstream statuses, and row
   counts before considering a test successful.
8. Update durable docs when architecture, schemas, operations, or stable findings
   change.

For Python edits, at minimum run `python -m py_compile` on changed modules. Add
focused tests when touching scheduling, parsing, identifier generation, data
normalization, or rate-limit behavior. A successful container build is not a
substitute for behavioral verification.

Use the repository's `.venv` when available. Production dependencies belong in
`requirements-cloud.txt`; local research/export dependencies belong in
`requirements-tools.txt`. Keep production images free of Playwright and other
research-only dependencies unless explicitly required.

## Cloud Operation Guardrails

Read-only inspection of GCP resources is allowed when needed to answer status
questions. Ask for explicit authorization before any action that changes cloud
state, cost, permissions, or production traffic, including:

- submitting a Cloud Build;
- deploying or updating a Cloud Run service/job;
- creating, changing, purging, pausing, or resuming a Cloud Tasks queue;
- executing a task-creator or backfill job;
- creating or altering BigQuery tables;
- writing or deleting GCS objects;
- changing IAM bindings or service-account permissions;
- launching a live Just Eat probe or full scrape.

Before an authorized launch, state the intended run id, input size, queue rate,
concurrency, hard-lock spacing, time windows, output tables/suffix, and expected
duration. After launch, report the concrete execution/queue identifiers needed
for monitoring.

Never expose service-account keys, access tokens, cookies, signed URLs, or other
credentials in code, logs, generated CSVs, documentation, or chat summaries.

## Git And Documentation Hygiene

- The worktree may contain user changes. Never revert, overwrite, or reformat
  unrelated modifications.
- Review `git status` before and after edits.
- Do not commit or push unless the user explicitly asks.
- Keep generated CSV, JSON, logs, browser profiles, build artifacts, and large
  data files out of Git.
- Do not delete historical research or run evidence merely because it is not on
  the current production import path.
- Update `docs/WORKLOG.md` for completed operational work.
- Update `docs/PROJECT_CONTEXT.md` only for stable architecture/current-state
  facts.
- Update `docs/NEXT_STEPS.md` when priorities or unresolved risks change.

When reporting results, distinguish verified current cloud state from estimates,
historical documentation, and inference. Include table grain and denominator
with row counts so that large numbers are not mistaken for unique postcodes or
unique restaurants.
