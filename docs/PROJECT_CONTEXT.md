# Project Context

## Purpose

The Delivery Availability Pipeline is research software for studying spatial and
temporal access to app-mediated food delivery in the United Kingdom. The public
repository contains the collection method and cloud pipeline, not the collected
data or live deployment configuration.

## Production Components

- `cloud_pipeline/create_tasks_cloud.py` creates deterministic postcode tasks
  and schedules them within explicit local-time intervals.
- `cloud_pipeline/run_task_creator_job.py` converts private deployment
  environment variables into task-creator arguments.
- `cloud_pipeline/task_worker_service.py` provides authenticated Flask endpoints,
  shared request limiting, an HTTP 429 circuit break, raw-response storage, and
  BigQuery writes.
- `cloud_pipeline/justeat_api.py` builds the listing request and normalises
  selected restaurant fields.
- `cloud_pipeline/create_menu_manifest_tasks.py` creates a separate manifest
  workflow for restaurant opening schedules.
- `cloud_pipeline/schema.py` and `cloud_pipeline/setup_tables.py` define and
  provision analytical and operational tables.

## Data Layers

The postcode listing workflow produces private raw responses and an observed
open-now snapshot. A controlled backfill of those raw responses can produce a
broader postcode-to-restaurant coverage map and a deduplicated restaurant
profile. The menu-manifest workflow produces manifest diagnostics and normalised
opening intervals.

The stable analytical entities are:

- static postcode and restaurant coverage;
- restaurant profile;
- restaurant opening schedule; and
- temporal observed availability.

Their grain and field semantics are documented in `DATA_MODEL.md`.

## Collection Semantics

Temporal snapshot rows represent restaurants that the source reported as
delivery-enabled, open for delivery, and not temporarily offline at capture
time. They do not represent all restaurants known to a postcode.

Dates assigned to one observation tag are capacity shards. Each postcode is
assigned once for that tag rather than once per date. Explicit scheduling and
worker checks are used to prevent tasks from drifting into a different research
window.

## Rate-Limit Design

Queue throughput limits task delivery, while a shared Cloud Storage reservation
controls upstream request starts across Cloud Run instances. The established
postcode research configuration used a 1300 millisecond global spacing and a
one-hour shared circuit break following HTTP 429. Menu-manifest traffic uses a
separate limiter because it is a different upstream surface.

These are documented historical safeguards, not an instruction to run a new
collection without a current review. See `ETHICAL_DATA_COLLECTION.md`.

## Repository Boundaries

- Production code must not import research probes or offline tools.
- Private data and deployment configuration remain outside Git.
- Real cloud identifiers are supplied only at deployment time.
- Generated data, logs, exports, browser profiles, and credentials are ignored.
- The sibling direct-grocery project is a separate codebase and data source.

## Public Release

The intended first archived software release is version `1.0.0`. Citation
metadata live in `CITATION.cff`; the Zenodo DOI will be added after the release
is archived. The software is MIT licensed by the University of Leeds. No dataset
is released with it.
