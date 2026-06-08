# Local Pipeline Prototype

The original project included a small local SQLite pipeline:

```text
local_pipeline/
  db.py
  create_jobs.py
  inspect_jobs.py
  worker.py
  export_full_postcodes.py
```

It was useful as a teaching/prototype version of the production idea:

```text
postcode CSV
  -> SQLite job table
    -> local worker
      -> Just Eat API
        -> raw JSON
        -> processed CSV
```

The local queue/worker code was not copied into this production-oriented repo
because it has been superseded by:

```text
Cloud Run Job task creator
  -> Cloud Tasks queue
    -> Cloud Run worker
      -> GCS raw JSON
      -> BigQuery tables
```

One piece was kept:

```text
tools/export_full_postcodes.py
```

That script builds `postcodes_full.csv` from the original England/Wales and
Scotland postcode spreadsheets. It remains useful for reconstructing the
pipeline input file.

