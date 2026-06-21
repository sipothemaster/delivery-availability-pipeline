# Worklog

## 2026-06-21

### Done

- Added a separate Just Eat menu manifest pipeline that avoids restaurant HTML:
  - endpoint: `/tasks/justeat-menu-manifest`
  - source: `https://menu-globalmenucdn.je-apis.com/{restaurant_unique_name}_uk_manifest.json`
  - fallback: `https://menu-globalmenucdn.je-apis.com/v2_2/{restaurant_unique_name}_uk_manifest.json`
- Added BigQuery table schemas for:
  - `menu_manifest_tasks`
  - `menu_manifest_results`
  - `restaurant_opening_times`
- Added task creation tooling:
  - `cloud_pipeline/create_menu_manifest_tasks.py`
  - `cloud_pipeline/run_menu_manifest_task_creator_job.py`
- Deployed isolated menu manifest worker:
  - service: `delivery-menu-manifest-worker-probe`
  - URL: `https://delivery-menu-manifest-worker-probe-280046610687.europe-west2.run.app`
  - limiter key: `justeat-menu-manifest-probe-20260621-1000ms`
  - limiter spacing: `1000ms`
- Ran local 100-restaurant manifest-only probe:
  - output: `data/output/menu_manifest_1s_probe_100_20260621.csv`
  - 100/100 HTTP 200
  - 100/100 original manifest
  - 0 fallback, 0 403, 0 429
  - p50 latency: 79 ms
  - p95 latency: 137 ms
- Ran cloud 1000-restaurant manifest-only probe:
  - run id: `menu-manifest-probe-20260621-1000b`
  - queue: `justeat-menu-manifest-probe-20260621`
  - 1000/1000 succeeded
  - 999 original manifest, 1 `v2_2` fallback
  - 0 failed, 0 403, 0 429, 0 5xx
  - p50 latency: 91 ms
  - p95 latency: 180 ms
  - max latency: 419 ms
  - `restaurant_opening_times` rows: 12,090
  - restaurants with opening times: 986/1000
- Checked the 14 restaurants with `opening_time_count=0`:
  - 8 had `is_delivery=true` in `postcode_restaurant_delivery_map`
  - 6 had `is_delivery=false`
  - conclusion: missing manifest opening times should be tracked separately and must not be treated as equivalent to `is_delivery=false`.
- Started full menu manifest run:
  - run id: `menu-manifest-full-20260621`
  - queue: `justeat-menu-manifest-full-20260621`
  - total restaurants/tasks: 100,850
  - Cloud Tasks rate: `1/s`
  - Cloud Tasks concurrency: `4`
  - worker hard lock: `1000ms`
  - first scheduled: 2026-06-21 21:37:39 Europe/London
  - last scheduled: 2026-06-23 01:38:28 Europe/London
  - expected completion: around 2026-06-23 01:45 Europe/London, allowing a few minutes for dispatch/write overhead
- Early full-run check:
  - first 241 results succeeded
  - all HTTP 200 original manifest
  - p50 latency: 86 ms
  - p95 latency: 171 ms

## 2026-06-17

### Done

- Designed and launched a new temporal Just Eat open-now snapshot run using the same worker/parser shape as `weekday_full`.
- Added explicit window interval support to `cloud_pipeline/create_tasks_cloud.py`:
  - `--window-intervals-json`
  - `--window-intervals-file`
- Added Cloud Run Job env support for:
  - `WINDOW_INTERVALS_JSON`
  - `WINDOW_INTERVALS_FILE`
  - `CREATE_TASK_WORKERS`
- Added controlled parallel Cloud Tasks creation via `--create-task-workers`.
- Added temporal schedule/config files:
  - `configs/temporal_snapshot_windows_202606.json`
  - `configs/task_creator_temporal_snapshot_202606_env.yaml`
- Updated build packaging so `configs/` is included in `Dockerfile.tasks` and `.gcloudignore`.
- Created new temporal BigQuery tables with suffix `_temporal_snapshot_202606`.
- Created new Cloud Tasks queue:
  - `delivery-scrape-temporal-202606`
  - rate `1/s`
  - concurrency `4`
- Deployed new Cloud Run worker:
  - service: `delivery-task-worker-temporal`
  - URL: `https://delivery-task-worker-temporal-280046610687.europe-west2.run.app`
  - worker limiter key: `justeat-temporal-202606-1300ms`
  - worker limiter spacing: `1300ms`
  - 429 ban circuit: `3600s`
- Ran a 40-postcode cloud smoke test:
  - run id: `temporal-smoke-20260617`
  - `40` started
  - `40` succeeded
  - `0` failed/deferred
  - diagnostics: `40` HTTP 200
  - snapshot rows: `16,007`
- Created and executed Cloud Run Job task creator:
  - job: `delivery-task-creator-temporal-202606`
  - execution: `delivery-task-creator-temporal-202606-8rf8h`
  - status: completed successfully
- Created production temporal manifest/tasks:
  - run id: `temporal-snapshot-20260617`
  - total tasks: `172,248`
  - `weekday_afternoon`: `43,062`
  - `weekday_evening`: `43,062`
  - `weekday_early_hours`: `43,062`
  - `saturday_peak`: `43,062`

### Temporal Windows

- `weekday_afternoon`: 2026-06-17/18/24/25, 14:00-18:00 Europe/London.
- `weekday_evening`: 2026-06-17/18/24/25, 18:30-22:30 Europe/London.
- `weekday_early_hours`: 2026-06-18/19/25/26, 00:00-04:00 Europe/London.
- `saturday_peak`: 2026-06-20/27, 14:00-22:00 Europe/London.

### Notes

- The temporal run uses `planned_window` as the tag field.
- The worker still writes only open-now delivery rows:
  - `isDelivery=true`
  - `isOpenNowForDelivery=true`
  - `isTemporarilyOffline=false`
- The new suffix keeps temporal output isolated from `weekday_full` and `weekend_full`.

## 2026-06-15

### Done

- Revisited the saved weekday raw JSON and confirmed the GCS raw files contain the full Just Eat API `restaurants` response, not only the open-now rows written to `restaurant_snapshots_weekday_full`.
- Verified sample raw files:
  - `ls42sw` existed in the older default `job_events` table, not in `job_events_weekday_full`.
  - `ls42nh` existed in `job_events_weekday_full`.
  - `ls42nh` raw JSON had 809 restaurants:
    - `isDelivery=true`: 654
    - `isDelivery=false`: 155
    - open delivery rows previously written to snapshot table: 429
- Designed a two-table static backfill model:
  - `postcode_restaurant_delivery_map`
  - `restaurant_profile`
- Reworked `tools/backfill_raw_pairings.py` to:
  - read saved `.json.gz` raw files from GCS
  - parse all restaurants from `response.restaurants`
  - write lightweight postcode-restaurant delivery map rows
  - write deduplicated restaurant profile rows
  - support either one `--raw-uri` or a BigQuery `--source-events-table`
- Added `tools/` to `Dockerfile.tasks` and `.gcloudignore` so Cloud Build includes the backfill script in the Cloud Run image.
- Validated locally on `ls42nh`:
  - `postcode_restaurant_delivery_map_test`: 809 rows
  - `restaurant_profile_test`: 809 rows
  - generated restaurant URLs and join query worked.
- Rebuilt and pushed the Cloud Run image:
  - digest: `sha256:ea586d1651fd4744ed066998e6d598192920c6c0c40d645522b1f50cc11c5373`
- Granted `scheduler-runner@delivery-availability-research.iam.gserviceaccount.com` the permissions needed for the backfill job:
  - `roles/storage.objectViewer` on `gs://delivery-availability-research-data-sipo`
  - `roles/bigquery.dataEditor`
  - `roles/bigquery.jobUser`
- Validated cloud execution on `ls42nh`:
  - `postcode_restaurant_delivery_map_cloud_test2`: 809 rows
  - `restaurant_profile_cloud_test2`: 809 rows
  - generated URLs and join query worked.
- Ran full weekday raw backfill:
  - Cloud Run Job: `raw-static-map-weekday-full-20260520`
  - execution: `raw-static-map-weekday-full-20260520-j5j4j`
  - status: completed successfully
  - duration: 1h28m32s
  - source events table: `job_events_weekday_full`
  - raw files processed: 43,062
  - snapshot label: `weekday_full_20260520`
  - output tables:
    - `postcode_restaurant_delivery_map`
    - `restaurant_profile`
- Final full weekday raw backfill output:
  - `postcode_restaurant_delivery_map`: 16,534,508 rows
  - map postcodes with at least one restaurant: 42,122
  - map distinct restaurants: 100,850
  - `is_delivery=true`: 13,144,889 rows
  - `is_delivery=false`: 3,389,619 rows
  - `restaurant_profile`: 100,850 rows
  - `restaurant_profile` URL completeness: 100,850/100,850
  - `restaurant_profile` location completeness: 100,850/100,850
  - `restaurant_profile` cuisine completeness: 100,850/100,850
  - restaurants skipped without id: 0

### Notes

- `restaurant_profile` is written at the end of the job after in-memory deduplication by `restaurant_id`; the raw JSON is still read only once.
- The current static map intentionally excludes `captured_at`, `raw_uri`, open-now/preorder/offline tags, and other dynamic fields from the main map table.
- Some raw jobs had zero restaurants: 43,062 raw jobs vs. 42,122 postcodes appearing in `postcode_restaurant_delivery_map`.
- ETA values include a few raw API outliers such as negative lower bounds and very high upper bounds; dashboard queries should filter or cap ETA where needed.

## 2026-05-21

### Done

- Checked `weekday-full-20260519` BigQuery output.
- Confirmed the two-day weekday full run completed successfully based on `job_events` and `job_diagnostics`:
  - 43,062/43,062 jobs started.
  - 43,062/43,062 jobs succeeded.
  - 0 HTTP 429.
  - 0 ban-circuit deferrals.
  - 43,062 `job_diagnostics` rows.
- Confirmed `restaurant_snapshots_weekday_full` output:
  - 10,361,486 snapshot rows.
  - 41,385 jobs/postcodes with at least one restaurant row.
  - 1,677 jobs/postcodes with zero restaurant rows.
  - 66,703 distinct `JustEatId`.
  - 66,704 distinct `Url`.
  - observed table size about 5.96 GB decimal / 5.55 GiB.
- Daily split:
  - 2026-05-19: 21,531 jobs, 21,531 succeeded, 0 429, 971 zero-result postcodes, 4,747,463 snapshot rows.
  - 2026-05-20: 21,531 jobs, 21,531 succeeded, 0 429, 706 zero-result postcodes, 5,614,023 snapshot rows.

### Findings

- The 1300ms worker global limiter with Cloud Tasks `1/s` and concurrency `4` survived a full two-day weekday run without triggering 429.
- `job_manifest_weekday_full.status` remained `pending` even though events and diagnostics show all jobs succeeded.
- Until fixed, use `job_events` and `job_diagnostics` as the source of truth for completion state.

### Deliveroo / Uber Eats Notes

- Deliveroo example `gcwf5uk59n55` decoded as a standard geohash:
  - latitude approximately `53.8130420353`
  - longitude approximately `-1.5874773078`
  - matches LS4 2SW.
- Uber Eats location token was decoded as:

```json
{"address":"LS4 2SW","reference":"ChIJQUq6zcxeeUgRccFm076FilE","referenceType":"google_places","latitude":53.8130421,"longitude":-1.5874772}
```

- Token format is base64url of URL-encoded JSON.
- Generated test URLs/tokens for fake reference, empty reference, and address+lat/lng-only variants.
- Open question: whether Uber Eats requires a valid Google Places `reference`.

### Code Hygiene Review

- Reviewed the repository for files unlikely to be used in future production.
- No files were deleted.
- Strong later deletion candidates:
  - `local_pipeline/`
  - root grocery scripts
  - old cloud polling path: `Dockerfile`, `cloud_pipeline/worker_cloud.py`, `cloud_pipeline/create_jobs_cloud.py`
  - `cloud_pipeline/upload_existing_sample.py`
  - `cloud_pipeline/task_creator_env.yaml`
- Confirm/archive before deleting:
  - JustEat research scripts such as `Probe_JustEat_API.py`, `Inspect_JustEat_Page.py`, `Inspect_JustEat_NextData.py`, `Search_JustEat_Chunks.py`, `Test_JustEat_Internal_API.py`, `Benchmark_JustEat.py`, and possibly `Scrape_JustEat.py`.

## 2026-05-18

### Done

- Checked the second day of `weekend-full-20260516`.
- Confirmed queue was actually `0.8/s` with `maxConcurrentDispatches=1`.
- Confirmed 2026-05-17 single-concurrency run had no new 429s.
- Confirmed single concurrency was safe but too slow for an 8-hour window:
  - 21,531/21,531 jobs succeeded.
  - Last success was 2026-05-18 01:04:49 Europe/London.
- Added millisecond-level worker diagnostics:
  - new `job_diagnostics` BigQuery table schema
  - API URL
  - API start/end timestamps
  - API latency
  - HTTP status
  - limiter wait/attempt metadata
- Added worker-level global Just Eat API token limiter using GCS state/lock objects.
- Deployed isolated private Cloud Run worker:
  - `delivery-task-worker-token-test`
- Created isolated Cloud Tasks queue:
  - `delivery-scrape-token-test`
  - rate `1/s`
  - concurrency `4`
- Ran boundary +-50 postcode token limiter tests using `_token_test` tables.

### Token Limiter Findings

- First reservation-slot limiter avoided 429 but did not strictly enforce actual API start spacing.
- Second lock-based limiter improved p50 spacing but still had lock-release timing drift.
- Final start-guard limiter test:
  - run id: `token-boundary-pm50-guard-20260518`
  - 404 tasks started
  - 404 tasks succeeded
  - 0 failed
  - 0 rejected
  - 0 HTTP 429
  - snapshot rows: 176,773
  - distinct restaurants: 3,821
  - API latency p50: 526 ms
  - API latency p95: 867 ms
  - API start gap p50: 1300 ms
  - API start gap p95: 1318 ms
  - min API start gap: 1220 ms
  - gaps under 1000 ms: 0
  - gaps under 1200 ms: 0

### Interpretation

- Worker-level global token limiting is promising.
- `1/s` Cloud Tasks with concurrency `4` plus worker token spacing around `1.3s` can process dense boundary postcodes without triggering 429 in this test.
- Because Cloud Run/GCS/Python scheduling introduces small timing jitter, production should not use exactly `1300ms` if the desired hard floor is `1300ms`.
- Safer production spacing candidate: `1350ms` or `1400ms`.

### 1100ms Test

- Tested `1100ms` worker limiter on the same 404 boundary +-50 dataset:
  - run id: `token-boundary-pm50-1100-20260518`
  - Cloud Tasks rate `1/s`
  - concurrency `4`
  - worker spacing `1100ms`
  - start guard `1000ms`
- Result:
  - 77 succeeded before first 429
  - 21 HTTP 429 failures recorded before the queue was stopped
  - first 429: `2026-05-18 16:13:35 UTC`, postcode `eh114rt`
  - last success before 429: `2026-05-18 16:13:34 UTC`, postcode `eh114qq`
  - min API gap: `1061ms`
  - no API gaps under `1000ms`
- Action taken:
  - paused `delivery-scrape-token-test`
  - purged remaining queued tasks
- Interpretation:
  - `1100ms` is too aggressive for this IP/API even without sub-1000ms bursts.
  - The safe boundary is likely above `1100ms`; `1300ms` remains the best tested candidate so far.

## 2026-05-17

### Done

- Continued Just Eat restaurant menu reverse engineering.
- Confirmed that full menu data is not obtained from rendered DOM alone.
- Confirmed full menu structure:
  - restaurant page HTML `__NEXT_DATA__` provides menu/category/item id skeleton and CDN filenames.
  - CDN `*_items.json` provides complete item/variation/price/image/nutrition data.
  - CDN `*_itemDetails.json` provides modifier groups, modifier options, and deal groups.
- Added `Reverse_JustEat_Menu.py` (now preserved as `research/justeat_menu_reverse/reverse_justeat_menu.py` in the cleaned repo).
- Ran `Reverse_JustEat_Menu.py` on 5 sample restaurant HTML files.
- Generated:
  - `data/output/restaurant_menu_reverse/summary.csv`
  - `data/output/restaurant_menu_reverse/menu_items.csv`
  - `data/output/restaurant_menu_reverse/modifier_options.csv`
  - `data/output/restaurant_menu_reverse/deal_options.csv`
- Verified script syntax with:
  - `.\.venv\Scripts\python.exe -m py_compile Reverse_JustEat_Menu.py`
- Discussed why pure rule-based "affordably healthy" classification may be too rigid.
- Created project handoff memory files:
  - `PROJECT_CONTEXT.md`
  - `WORKLOG.md`
  - `NEXT_STEPS.md`

### Menu Reverse Engineering Findings

- One restaurant normally requires about 3 requests:
  - restaurant HTML
  - CDN `items.json`
  - CDN `itemDetails.json`
- This is per restaurant, not per menu item.
- CDN data seems static and likely lower pressure than postcode availability API, but should still be tested gently.
- `menu/dynamic` API contains live state/rating/offline/fees data but not the complete menu.
- `DealItemVariationId` in deal groups can be joined back to item variations in `items.json`.

### Operational Notes

- User reported some VS Code Codex conversations can get stuck at "loading model".
- Mitigation: keep durable project memory in repo files rather than relying on old chat state.
- New Codex conversations should start by reading:
  - `PROJECT_CONTEXT.md`
  - `WORKLOG.md`
  - `NEXT_STEPS.md`

### Known Issue

- `git` was not available in the current PowerShell PATH during this session, so no `git diff` command was run.

## 2026-05-16

### Done

- Created and ran weekend full Cloud Tasks production setup.
- Created separate weekend production queue and BigQuery suffix instead of modifying original queue/table setup.
- User clarified weekend run should be one `weekend` tag, not morning/afternoon split.
- Weekend window set to 12:00-20:00 Europe/London over two days.
- Investigated first-day 429 behavior and exported episode/window CSVs.
- Ran episode-boundary stress test with original parameters.
- User manually changed weekend full queue concurrency from 2 to 1 in GCP Console.

### Key Findings

- 429 episodes appear to ban the IP for about one hour.
- Concurrency `2` likely caused same-second burst risk.
- Concurrency `1` is currently preferred to avoid 429, even though the 8-hour schedule has limited buffer at `0.8/s`.
