# Next Steps

Last updated: 2026-06-21

For a new Codex conversation, start with:

```text
Read PROJECT_CONTEXT.md, WORKLOG.md, and NEXT_STEPS.md, then continue from the current project state.
```

## Immediate Checks

1. Monitor full menu manifest run:
   - run id: `menu-manifest-full-20260621`
   - queue: `justeat-menu-manifest-full-20260621`
   - total tasks: 100,850
   - expected completion: around 2026-06-23 01:45 Europe/London
   - check `menu_manifest_results` for:
     - succeeded/failed counts
     - HTTP 403/429/5xx
     - original vs `v2_2` fallback counts
     - `opening_time_count=0`
   - check `restaurant_opening_times` row count and distinct restaurant count.
2. Monitor `temporal-snapshot-20260617`:
   - first active window: `weekday_afternoon`, 2026-06-17 14:00-18:00 Europe/London.
   - use `job_events_temporal_snapshot_202606` and `job_diagnostics_temporal_snapshot_202606`.
   - check started/succeeded/failed/deferred counts.
   - confirm `http_status=200` and no 429.
   - confirm `restaurant_snapshots_temporal_snapshot_202606.planned_window` values are the intended tags.
3. Validate and document final weekday static map output:
   - `postcode_restaurant_delivery_map`: 16,534,508 rows.
   - `restaurant_profile`: 100,850 rows.
   - join sanity check by postcode, e.g. `ls42nh`.
   - compare old open parser counts against the new all-restaurant map.
   - decide how dashboard queries should handle ETA outliers.
4. Build an enriched BigQuery view for EDA/dashboard use.
5. Decide whether to backfill the weekend raw data into the same two-table static model.
6. Fix or investigate manifest status updates:
   - `weekday-full-20260519` has all jobs succeeded in `job_events`/`job_diagnostics`, but `job_manifest_weekday_full.status` stayed `pending`.
   - Until fixed, use events/diagnostics as completion truth.
7. Decide whether to standardize production Just Eat rate settings at:
   - Cloud Tasks `1/s`
   - concurrency `4`
   - worker global limiter spacing `1300ms`
   - 429 ban circuit `3600s`
8. Query/export final comparison summary for:
   - `weekend-full-20260516`
   - `weekday-full-20260519`
9. Decide whether to retry the 7 weekend postcodes that failed during the 2026-05-16 429 ban period.

## Static Map Backfill

Current static table design:

- `postcode_restaurant_delivery_map`: lightweight postcode-restaurant delivery coverage map.
- `restaurant_profile`: unique restaurant static/profile information and generated Just Eat restaurant URL.

Near-term backfill tasks:

1. Run the same backfill for weekend raw data if final weekend `raw_uri` coverage is complete enough.
2. Add dashboard-safe filters or derived fields for ETA outliers.
3. Consider whether to keep old test tables or delete them later:
   - `postcode_restaurant_delivery_map_test`
   - `restaurant_profile_test`
   - `postcode_restaurant_delivery_map_cloud_test`
   - `postcode_restaurant_delivery_map_cloud_test2`
   - `restaurant_profile_cloud_test`
   - `restaurant_profile_cloud_test2`
4. Build an enriched view for dashboard/EDA:

```sql
SELECT
  p.*,
  r.restaurant_name,
  r.restaurant_url,
  r.cuisine_names,
  r.rating_star,
  r.city
FROM postcode_restaurant_delivery_map p
LEFT JOIN restaurant_profile r
USING (restaurant_id)
```

## Weekend Run Follow-Up

- Weekend first day had 429 episodes under original higher burst risk.
- Weekend second day with single concurrency had no new 429 but overran the 12:00-20:00 window.
- Full weekday run with worker global limiter had 0 429 and completed within the intended windows.
- Current best production candidate is Cloud Tasks concurrency `4` with worker-level global API spacing, not single-concurrency-only dispatch.
- Do not use `1100ms` spacing for production: a 404-postcode boundary test triggered 429 after 77 successes even though no API gap fell below `1000ms`.
- Export final summary for:
  - successful postcode count
  - no-restaurant postcode count
  - failed postcode count
  - distinct restaurant count
  - duplicate restaurant/postcode coverage density

## Menu Pipeline Design

Current state: the manifest/opening-times layer is now implemented and the full run is active. The next menu work should build on `restaurant_profile.restaurant_unique_name`, `menu_manifest_results`, and `restaurant_opening_times`.

Current manifest approach:

1. Deduplicate restaurants by Just Eat restaurant id and URL.
2. Fetch CDN manifest directly:
   - `{restaurant_unique_name}_uk_manifest.json`
   - fallback `v2_2/{restaurant_unique_name}_uk_manifest.json`
3. Write:
   - `menu_manifest_results`
   - `restaurant_opening_times`
4. Do not fetch restaurant HTML unless CDN/API routes fail.

Next menu-items phase:

1. Use `menu_manifest_results.items_url`.
2. Exclude grocery/convenience stores from item scraping if product-level grocery data is not needed.
3. Fetch `items.json` only for non-grocery restaurants.
4. Defer `itemDetails.json` / modifier groups until needed.
5. Track failures with source status:
   - `manifest_missing_items_url`
   - `cdn_items_url_failed`
   - `items_parsed`

Potential normalized menu tables:

- `restaurant_menu_manifest`
- `restaurant_menu_items`
- `restaurant_menu_variations`
- `restaurant_menu_modifier_groups` later
- `restaurant_menu_modifier_options` later
- `restaurant_menu_deal_groups` later
- `restaurant_menu_deal_options` later

## Rate Limit Caution

- Menu CDN manifest has passed local 100 and cloud 1000 tests at `1/s` with 0 failures/429.
- Full manifest run is using `1/s`, concurrency `4`, worker hard lock `1000ms`.
- Continue monitoring HTTP 403/429/5xx before increasing rate.
- Watch for HTTP 403/429 from:
  - `www.just-eat.co.uk`
  - `menu-globalmenucdn.je-apis.com`
  - `uk.api.just-eat.io`

## Affordably Healthy Analysis

Recommended MVP:

1. Build item role classifier:
   - `main`
   - `side`
   - `drink`
   - `dessert`
   - `sauce`
   - `modifier`
   - `deal`
   - `unknown`
2. Parse price and kcal from menu CSV.
3. Use semantic/model classification for health class:
   - `healthy`
   - `moderate`
   - `unhealthy`
   - `unclear`
4. Define affordability by local price distribution, not only fixed thresholds.
5. Aggregate per restaurant:
   - `affordable_healthy_main_count`
   - `cheapest_healthy_main_price`
   - `healthy_main_share`
   - `median_main_price`
   - `high_kcal_main_share`

## Other Platform Reverse Engineering

Deliveroo:

1. Confirm listing API request shape after setting a postcode/geohash.
2. Test whether standard postcode lat/lng -> geohash is sufficient.
3. If sufficient, implement a small `deliveroo_api.py` probe before any cloud integration.

Uber Eats:

1. Test generated `pl` token variants:
   - fake Google Places reference
   - empty reference
   - no `reference`/`referenceType`, only address + lat/lng
2. Determine whether Uber Eats backend requires a valid Google Places reference.
3. If valid reference is required, decide between:
   - Google Geocoding/Places API
   - reverse engineering Uber's own location resolve endpoint
4. Only after request shape is stable, design a provider module and avoid mixing it into Just Eat worker code too early.

## Code Hygiene

- Do not delete files yet; user explicitly deferred cleanup.
- Later cleanup should start with strong deletion candidates:
  - `local_pipeline/`
  - root grocery scripts
  - old cloud polling path: `Dockerfile`, `cloud_pipeline/worker_cloud.py`, `cloud_pipeline/create_jobs_cloud.py`
  - one-off helpers such as `cloud_pipeline/upload_existing_sample.py`
  - old generic env file `cloud_pipeline/task_creator_env.yaml`
- Before deleting JustEat research scripts, either archive them under a research folder or confirm they are no longer needed for menu/API reverse engineering.

## Hygiene

- Update `WORKLOG.md` at the end of each work session.
- Update `PROJECT_CONTEXT.md` only when stable facts change.
- Update `NEXT_STEPS.md` when priorities shift.
