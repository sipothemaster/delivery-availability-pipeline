# Just Eat API Reverse Engineering Notes

This folder preserves the exploratory scripts that led to the production
`cloud_pipeline/justeat_api.py` implementation.

These scripts are not part of the Cloud Run production path. They are kept so the
reverse-engineering trail remains auditable and repeatable.

## Suggested Reading Order

1. `Inspect_JustEat_Page.py`
   - Opens a Just Eat area page and records embedded scripts and `__NEXT_DATA__`.
   - Useful for checking whether restaurant data is server-rendered or fetched later.

2. `Inspect_JustEat_NextData.py`
   - Walks `__NEXT_DATA__` looking for restaurant-like paths and API configuration.
   - Helped identify the client config and API base candidates.

3. `Search_JustEat_Chunks.py`
   - Searches Next.js chunk scripts for keywords such as `privatePlapi`,
     `partnerlisting`, `byslug`, `open_now`, and `serviceType`.
   - Useful when Just Eat changes frontend bundles.

4. `Probe_JustEat_API.py`
   - Captures browser network responses while loading an area page.
   - Summarizes JSON payloads and highlights restaurant-like responses.

5. `Test_JustEat_Internal_API.py`
   - Tries likely internal/public API URL variants against one postcode.
   - Helped confirm the enriched listing endpoint.

6. `Scrape_JustEat.py`
   - Earlier combined scraper containing both browser scraping and API helpers.
   - Production logic was later distilled into `cloud_pipeline/justeat_api.py`.

7. `Benchmark_JustEat.py`
   - Benchmarks browser scroll/loading strategies.
   - Kept for historical comparison; not used in production.

## Main Production Endpoint Found

The production client currently uses a Just Eat listing endpoint shaped like:

```text
https://uk.api.just-eat.io/discovery/uk/restaurants/enriched/byslug/{postcode-or-area-slug}?serviceType=delivery
```

The current production parser lives in:

```text
cloud_pipeline/justeat_api.py
```

## Research Dependencies

Install research dependencies only when running these scripts locally:

```powershell
pip install -r requirements-tools.txt
playwright install chromium
```

Most scripts write outputs under `data/output/`, which is intentionally ignored
by git.

