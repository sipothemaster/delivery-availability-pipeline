# Project Context

Last updated: 2026-06-15

This file is the handoff memory for new Codex conversations. Read this first, then read `WORKLOG.md` and `NEXT_STEPS.md`.

## Goal

Build and operate a UK delivery availability scraping pipeline, focused on Just Eat postcode coverage and later restaurant menu analysis.

Current major threads:

- Full postcode delivery availability scrape via GCP Cloud Tasks and Cloud Run.
- Avoid Just Eat 429 responses because observed 429 episodes appear to ban the Cloud Run egress IP for about one hour.
- Reverse engineer Just Eat restaurant menu data sources.
- Explore an "affordably healthy" restaurant/menu score using scraped menu data.

## Repository Map

Important files:

- `cloud_pipeline/create_tasks_cloud.py`: Creates Cloud Tasks for postcode/time-window jobs.
- `cloud_pipeline/task_worker_service.py`: Cloud Run worker endpoint that executes one scrape task.
- `cloud_pipeline/justeat_api.py`: Just Eat postcode API client/parser.
- `cloud_pipeline/config.py`: Shared GCP and BigQuery config.
- `cloud_pipeline/run_task_creator_job.py`: Cloud Run Job entrypoint for creating tasks in the cloud.
- `cloud_pipeline/setup_tables.py`: BigQuery table setup.
- `tools/backfill_raw_pairings.py`: Reads saved Just Eat raw `.json.gz` files from GCS and backfills static postcode-restaurant delivery map and restaurant profile tables.
- `configs/task_creator_weekend_env.yaml`: Weekend full-run task creator environment.
- `configs/task_creator_weekday_env.yaml`: Weekday full-run task creator environment.
- `research/justeat_menu_reverse/reverse_justeat_menu.py`: Reverse-engineered menu extractor for restaurant pages.

Important local outputs:

- `data/output/weekend_full_429_ban_episodes.csv`
- `data/output/weekend_full_429_episode_boundary_pm5s_windows.csv`
- `data/output/restaurant_reverse_samples/`
- `data/output/restaurant_menu_reverse/`

## GCP Architecture

Main architecture:

```text
Cloud Run Job task creator
  -> Cloud Tasks queue
    -> Cloud Run worker
      -> Just Eat API / page / CDN
        -> GCS raw output
        -> BigQuery tables
```

Project/resource defaults:

- GCP project: `delivery-availability-research`
- Region/location: `europe-west2`
- GCS bucket: `delivery-availability-research-data-sipo`
- BigQuery dataset: `delivery_availability`

Use `gcloud.cmd` from PowerShell if `gcloud` itself is not resolved.

## Weekend Full Run

Production weekend run:

- Run id: `weekend-full-20260516`
- Full postcode input: `gs://delivery-availability-research-data-sipo/input/postcodes_full.csv`
- Full postcode count: 43,062
- Time windows:
  - 2026-05-16 12:00-20:00 Europe/London
  - 2026-05-17 12:00-20:00 Europe/London
- Tag: `weekend`
- Design intent: one weekend tag, no separate morning/afternoon tags.

Weekend GCP resources:

- Cloud Tasks queue: `delivery-scrape-weekend-full`
- Cloud Run worker: `delivery-task-worker-weekend`
- Cloud Run Job creator: `delivery-task-creator-weekend`
- Worker URL seen during deploy:
  - `https://delivery-task-worker-weekend-280046610687.europe-west2.run.app`
  - Service describe also showed `https://delivery-task-worker-weekend-ami6mx2gwa-nw.a.run.app`
- BigQuery table suffix: `_weekend_full`

Weekend BigQuery tables:

- `delivery_availability.job_manifest_weekend_full`
- `delivery_availability.job_events_weekend_full`
- `delivery_availability.restaurant_snapshots_weekend_full`
- `delivery_availability.scrape_jobs_weekend_full`

## Weekday Full Run

Production weekday run:

- Run id: `weekday-full-20260519`
- Full postcode input: `gs://delivery-availability-research-data-sipo/input/postcodes_full.csv`
- Full postcode count: 43,062
- Time windows:
  - 2026-05-19 12:00-20:00 Europe/London
  - 2026-05-20 12:00-20:00 Europe/London
- Tag: `weekday`

Weekday GCP resources:

- Cloud Tasks queue: `delivery-scrape-weekday-full`
- Cloud Run worker: `delivery-task-worker-weekday`
- Cloud Run Job creator: `delivery-task-creator-weekday`
- Worker URL observed from service describe:
  - `https://delivery-task-worker-weekday-ami6mx2gwa-nw.a.run.app`
- BigQuery table suffix: `_weekday_full`

Weekday worker settings:

- Cloud Tasks rate: `1/s`
- Cloud Tasks concurrency: `4`
- worker global limiter spacing: `1300ms`
- worker start guard: `1000ms`
- limiter key: `justeat-weekday-1300ms`
- 429 ban circuit enabled:
  - ban key: `justeat-weekday-1300ms`
  - ban duration: `3600s`

Weekday result checked on 2026-05-21:

- 43,062/43,062 jobs had `started` and `succeeded` events.
- 0 HTTP 429.
- 0 ban-circuit deferrals.
- 43,062 `job_diagnostics` rows.
- 10,361,486 `restaurant_snapshots_weekday_full` rows.
- 41,385 jobs/postcodes had at least one restaurant row.
- 1,677 jobs/postcodes had zero restaurant rows.
- 66,703 distinct `JustEatId`.
- 66,704 distinct `Url`.
- Table size observed for `restaurant_snapshots_weekday_full`: about 5.96 GB decimal / 5.55 GiB.

Daily split:

- 2026-05-19: 21,531 jobs, 21,531 succeeded, 0 429, 971 zero-result postcodes, 4,747,463 snapshot rows.
- 2026-05-20: 21,531 jobs, 21,531 succeeded, 0 429, 706 zero-result postcodes, 5,614,023 snapshot rows.

Known issue:

- `job_manifest_weekday_full.status` remained `pending` for all rows even though `job_events` and `job_diagnostics` show the run succeeded. Use events/diagnostics as the source of truth until manifest update logic is fixed.

Raw JSON/backfill status:

- All 43,062 successful weekday jobs have `job_events_weekday_full.raw_uri`.
- Raw files are stored in GCS under paths like:

```text
gs://delivery-availability-research-data-sipo/raw/provider=just_eat/date=2026-05-20/window=weekday/postcode=ls42nh_job=24e24774-04ab-5cdf-bc60-90040bb08f7e.json.gz
```

- These raw files contain the full API `response.restaurants` list.
- `restaurant_snapshots_weekday_full` only contains rows that passed the old open-delivery parser:

```text
isDelivery=true
isOpenNowForDelivery=true
isTemporarilyOffline=false
```

- Therefore `restaurant_snapshots_weekday_full` is an open/current-delivery table, not the full postcode coverage map.
- Current static raw backfill Cloud Run Job:
  - job: `raw-static-map-weekday-full-20260520`
  - execution: `raw-static-map-weekday-full-20260520-j5j4j`
  - source events table: `job_events_weekday_full`
  - raw files: 43,062
  - snapshot label: `weekday_full_20260520`
  - output tables:
    - `delivery_availability.postcode_restaurant_delivery_map`
    - `delivery_availability.restaurant_profile`

Static backfill table model:

```text
postcode_restaurant_delivery_map
  snapshot_label
  postcode
  restaurant_id
  is_delivery
  delivery_fee
  minimum_delivery_value
  delivery_eta_lower_minutes
  delivery_eta_upper_minutes
  drive_distance_meters

restaurant_profile
  restaurant_id
  restaurant_name
  restaurant_unique_name
  restaurant_url
  address_first_line
  city
  postal_code
  latitude
  longitude
  cuisine_names
  cuisine_unique_names
  rating_count
  rating_star
  logo_url
```

Restaurant URLs are not directly present as `url` in the postcode API JSON. They are generated from `uniqueName`:

```text
https://www.just-eat.co.uk/restaurants-{restaurant_unique_name}/menu
```

Sample `ls42nh` weekday raw JSON:

- Total restaurants in raw: 809.
- `isDelivery=true`: 654.
- `isDelivery=false`: 155.
- Open delivery rows under the old parser: 429.

Relevant config support:

- `cloud_pipeline/config.py` supports `BQ_TABLE_SUFFIX`.
- `cloud_pipeline/create_tasks_cloud.py` supports daily windows:
  - `--daily-start-date`
  - `--daily-days`
  - `--daily-local-start-time`
  - `--daily-local-end-time`

Current queue rate history:

- Original weekend full queue was created around `0.8/s` and concurrency `2`.
- After first-day 429 analysis, user manually changed `delivery-scrape-weekend-full` in GCP Console to concurrency `1`.
- Do not trust YAML alone for current queue state; confirm with `gcloud tasks queues describe` if needed.

## 429 Findings

User's operational priority: avoid 429 entirely. Retry behavior is not the main issue because once 429 appears, the IP appears banned for about one hour.

Observed weekend full ban episodes from `job_events_weekend_full`:

1. First 429 at `2026-05-16 15:22:42 UTC`, postcode `eh112lj`; next real success around `16:22:43 UTC`.
2. First 429 at `2026-05-16 17:13:22 UTC`, postcode `g157rl`; next real success around `18:13:32 UTC`.
3. First 429 at `2026-05-16 18:18:55 UTC`, postcode `g467pf`; next real success around `19:19:06 UTC`.
4. First 429 at `2026-05-16 20:27:49 UTC`, postcode `ig80tt`; next real success around `21:27:55 UTC`.

Interpretation:

- Each episode lasted about 3600 seconds.
- Concurrency `2` can create same-second burst pairs when task durations vary.
- Episode-boundary stress test with original parameters triggered 429 quickly around the EH11 boundary.
- Current safest mitigation is queue concurrency `1`, keeping rate around `0.8/s`.
- Workload math: 21,531 postcodes per 8-hour day requires about `0.748/s`; `0.8/s` has only about 7% buffer, but avoiding one-hour bans is more important.

2026-05-18 token limiter test:

- Built a worker-level global Just Eat API limiter using GCS state/lock objects.
- Added `job_diagnostics` BigQuery table with millisecond API timing diagnostics.
- Isolated test resources:
  - Cloud Run worker: `delivery-task-worker-token-test`
  - Cloud Tasks queue: `delivery-scrape-token-test`
  - BigQuery suffix: `_token_test`
- Final boundary +-50 test run id: `token-boundary-pm50-guard-20260518`
- Test parameters:
  - Cloud Tasks rate: `1/s`
  - Cloud Tasks concurrency: `4`
  - worker limiter spacing: `1300ms`
  - worker start guard: `1000ms`
- Final result:
  - 404/404 succeeded
  - 0 HTTP 429
  - API start gap p50: `1300ms`
  - API start gap p95: `1318ms`
  - min API start gap: `1220ms`
  - no gaps under `1200ms`
- Interpretation:
  - Worker-level global token limiting is promising.
  - Because of small Cloud Run/GCS/Python scheduling jitter, use `1350ms` or `1400ms` if production needs a hard effective floor around `1300ms`.
- Follow-up 1100ms boundary test:
  - run id: `token-boundary-pm50-1100-20260518`
  - first 429 occurred after 77 successes.
  - first 429: `2026-05-18 16:13:35 UTC`, postcode `eh114rt`.
  - min observed API gap was `1061ms`, with no gaps under `1000ms`.
  - Queue was paused and purged immediately.
  - Conclusion: `1100ms` is too aggressive; the production candidate should stay closer to `1300ms` unless a later larger test proves otherwise.

Important exports:

- `data/output/weekend_full_429_ban_episodes.csv`
- `data/output/weekend_full_429_episode_boundary_pm5s_windows.csv`
- `data/input/postcodes_episode_boundary_pm50.csv`
- `data/input/postcodes_episode_boundary_pm50_audit.csv`
- `data/output/episode_boundary_test_first_429_pm5s.csv`

## Data Volume Notes

At one earlier snapshot of the weekend run:

- `job_manifest_weekend_full`: 43,062 rows.
- `restaurant_snapshots_weekend_full`: about 4.3 million snapshot rows.
- Distinct Just Eat restaurant ids seen: about 38.6k at that time.
- Some succeeded jobs have `processed_rows=0`. User manually checked samples and confirmed these are real no-delivery/no-open-restaurant postcodes, not parser failures.

Example `processed_rows=0` postcodes manually checked:

- `bt345tw`
- `ky169pd`
- `iv262sz`
- `gl127ld`
- `ky103fb`

## Restaurant Menu Reverse Engineering

Sampled 5 restaurants from weekend snapshot data:

- `202687`: Basil Wood Oven Pizzeria
- `178430`: Prairie Hotel - Sizzling Pubs
- `315913`: Seoulful Bites
- `217881`: Costa Coffee Halesowen
- `184485`: Domino's - Falkirk - Grangemouth

Core discovery:

```text
Restaurant HTML page
  -> __NEXT_DATA__
    -> props.appProps.preloadedState.menu.restaurant.cdn.restaurant
      -> menus/categories/itemIds
      -> itemsUrl
      -> itemDetailsUrl
      -> truncatedUrl
      -> CDN base

CDN items JSON
  -> Items[]
    -> item details, variations, price, images, labels, kcal, modifier group ids, deal group ids

CDN itemDetails JSON
  -> ModifierGroups[]
  -> ModifierSets[]
  -> DealGroups[]
```

CDN base:

```text
https://menu-globalmenucdn.je-apis.com
```

Dynamic API observed:

```text
https://uk.api.just-eat.io/restaurant/uk/{restaurant_id}/menu/dynamic?orderTime=...
```

That dynamic API returns current state/rating/offline ids/fees but not the full menu.

For one restaurant, complete menu usually needs:

```text
1 restaurant HTML request
+ 1 items.json CDN request
+ 1 itemDetails.json CDN request
```

It is not one request per item.

Menu extraction script:

- `research/justeat_menu_reverse/reverse_justeat_menu.py`

Generated outputs:

- `data/output/restaurant_menu_reverse/summary.csv`
- `data/output/restaurant_menu_reverse/menu_items.csv`
- `data/output/restaurant_menu_reverse/modifier_options.csv`
- `data/output/restaurant_menu_reverse/deal_options.csv`

Sample extraction results:

- Prairie: 253 items, 208 modifier groups.
- Domino's: 553 items, 1933 modifier groups, 20 deal groups.
- Basil: 96 items, 10 modifier groups.
- Costa: 314 items, 443 modifier groups, 48 deal groups.
- Seoulful: 30 items, 7 modifier groups.

Fallback strategy for menu scraping:

1. Parse restaurant HTML `__NEXT_DATA__`.
2. Use CDN `itemsUrl` and `itemDetailsUrl` if present.
3. If CDN URL is missing, fall back to inline `cdn.items` inside `__NEXT_DATA__`.
4. If still missing, mark restaurant as `menu_unresolved` for separate reverse engineering.

## Affordably Healthy Analysis Ideas

Avoid pure hard-coded rules as final judgment. Recommended hybrid pipeline:

1. Rule-based structure parsing:
   - classify item role: `main`, `side`, `drink`, `dessert`, `sauce`, `modifier`, `deal`, `unknown`
   - parse price
   - parse kcal
   - identify obvious non-meal categories
2. Model/semantic classification:
   - decide if an item is a reasonably healthy main meal
   - output class and confidence
3. Affordability by data:
   - absolute price threshold
   - local percentile within postcode/area
   - cuisine-specific comparison if possible
4. Restaurant aggregation:
   - affordable healthy main count
   - cheapest healthy main price
   - healthy main share
   - median main price
   - high-kcal main share

Potential restaurant labels:

- A: multiple affordable healthy mains.
- B: at least one affordable healthy main.
- C: healthy choices exist but expensive.
- D: affordable but mostly unhealthy.
- E: limited/unclear/no healthy affordable options.

## Other Platform Location Reverse Engineering

Deliveroo:

- Example geohash: `gcwf5uk59n55`.
- Decodes as standard geohash to approximately:
  - latitude `53.8130420353`
  - longitude `-1.5874773078`
- This matches the LS4 2SW area.
- Implication: if Deliveroo accepts standard geohash in its listing API, the pipeline can use postcode lat/lng -> geohash directly.

Uber Eats:

- Example location token:
  - `JTdCJTIyYWRkcmVzcyUyMiUzQSUyMkxTNCUyMDJTVyUyMiUyQyUyMnJlZmVyZW5jZSUyMiUzQSUyMkNoSUpRVXE2emN4ZWVVZ1JjY0ZtMDc2RmlsRSUyMiUyQyUyMnJlZmVyZW5jZVR5cGUlMjIlM0ElMjJnb29nbGVfcGxhY2VzJTIyJTJDJTIybGF0aXR1ZGUlMjIlM0E1My44MTMwNDIxJTJDJTIybG9uZ2l0dWRlJTIyJTNBLTEuNTg3NDc3MiU3RA`
- Decoding layers:
  - base64url
  - URL-decoded JSON
- Decoded JSON:

```json
{"address":"LS4 2SW","reference":"ChIJQUq6zcxeeUgRccFm076FilE","referenceType":"google_places","latitude":53.8130421,"longitude":-1.5874772}
```

- `reference` appears to be a Google Places id/reference.
- Open question: whether Uber Eats backend actually requires a valid reference or only uses address + lat/lng.
- Test tokens were generated for:
  - fake reference
  - empty reference
  - no `reference`/`referenceType`, only address + lat/lng

## Code Hygiene Notes

Do not delete files yet; user asked to defer cleanup. Current cleanup candidates for later:

- Strong delete candidates:
  - `local_pipeline/`
  - root grocery scripts: `Scrape_AmazonFresh.py`, `Scrape_ASDA.py`, `Scrape_Chopchop.py`, `Scrape_Iceland.py`, `Scrape_Morrisons.py`, `Scrape_Ocado.py`, `Scrape_Sainsburys.py`, `Scrape_Waitrose.py`
  - old cloud polling path: `Dockerfile`, `cloud_pipeline/worker_cloud.py`, `cloud_pipeline/create_jobs_cloud.py`
  - one-off migration/sample helper: `cloud_pipeline/upload_existing_sample.py`
  - old generic env file: `cloud_pipeline/task_creator_env.yaml`
  - `__pycache__/`
- Confirm/archive before deleting:
  - `Scrape_JustEat.py`
  - `Probe_JustEat_API.py`
  - `Inspect_JustEat_Page.py`
  - `Inspect_JustEat_NextData.py`
  - `Search_JustEat_Chunks.py`
  - `Test_JustEat_Internal_API.py`
  - `Benchmark_JustEat.py`
  - `cloud_pipeline/create_smoke_tasks_cloud.py`
  - historical run env files after parameters are captured in docs
- Keep for production/current work:
  - `cloud_pipeline/task_worker_service.py`
  - `cloud_pipeline/justeat_api.py`
  - `cloud_pipeline/create_tasks_cloud.py`
  - `cloud_pipeline/create_week_jobs_cloud.py`
  - `cloud_pipeline/run_task_creator_job.py`
  - `cloud_pipeline/schema.py`
  - `cloud_pipeline/setup_tables.py`
  - `cloud_pipeline/config.py`
  - `Dockerfile.tasks`
  - `cloudbuild.tasks.yaml`
  - `requirements-cloud.txt`
  - `tools/backfill_raw_pairings.py`
  - `research/justeat_menu_reverse/reverse_justeat_menu.py`
