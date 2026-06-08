import argparse
import csv
import io
import json
import re
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from google.api_core.exceptions import AlreadyExists
from google.cloud import bigquery, storage, tasks_v2
from google.protobuf import timestamp_pb2

from cloud_pipeline import config
from cloud_pipeline.create_week_jobs_cloud import (
    DEFAULT_WINDOW_NAMES,
    build_windows,
    parse_local_date,
    schedule_across_intervals,
)


LONDON = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")


def clean_postcode(postcode):
    return re.sub(r"\s+", "", str(postcode)).lower()


def utc_now():
    return datetime.now(UTC).replace(microsecond=0)


def parse_gcs_uri(uri):
    if not uri.startswith("gs://"):
        raise ValueError(f"Not a GCS URI: {uri}")
    without_scheme = uri.removeprefix("gs://")
    bucket_name, _, blob_name = without_scheme.partition("/")
    if not bucket_name or not blob_name:
        raise ValueError(f"GCS URI must look like gs://bucket/path/file.csv: {uri}")
    return bucket_name, blob_name


def open_postcode_file(path):
    path_text = str(path)
    if path_text.startswith("gs://"):
        bucket_name, blob_name = parse_gcs_uri(path_text)
        client = storage.Client(project=config.PROJECT_ID)
        text = client.bucket(bucket_name).blob(blob_name).download_as_text(encoding="utf-8-sig")
        return io.StringIO(text)
    return open(path, newline="", encoding="utf-8-sig")


def read_postcodes(path, limit=None):
    with open_postcode_file(path) as handle:
        reader = csv.DictReader(handle)
        if "postcode" not in reader.fieldnames:
            raise ValueError("Input CSV must contain a 'postcode' column.")
        postcodes = [clean_postcode(row["postcode"]) for row in reader if row.get("postcode")]
    if limit:
        postcodes = postcodes[:limit]
    return postcodes


def default_run_id():
    return f"justeat-{datetime.now(LONDON).date().isoformat()}"


def stable_job_id(run_id, provider, planned_window, postcode):
    key = f"{run_id}|{provider}|{planned_window}|{postcode}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def task_id_for_job(job_id):
    return f"job-{job_id}"


def task_name_for_job(queue_id, job_id):
    return (
        f"projects/{config.PROJECT_ID}/locations/{config.LOCATION}/"
        f"queues/{queue_id}/tasks/{task_id_for_job(job_id)}"
    )


def immediate_schedule(count):
    now = utc_now()
    return [now + timedelta(seconds=index) for index in range(count)]


def local_window_interval(start_text, end_text):
    start = datetime.fromisoformat(start_text)
    end = datetime.fromisoformat(end_text)
    if start.tzinfo is None:
        start = start.replace(tzinfo=LONDON)
    else:
        start = start.astimezone(LONDON)
    if end.tzinfo is None:
        end = end.replace(tzinfo=LONDON)
    else:
        end = end.astimezone(LONDON)
    if end <= start:
        raise ValueError("--custom-local-end must be later than --custom-local-start.")
    return start.replace(tzinfo=None).isoformat(), end.replace(tzinfo=None).isoformat()


def custom_windows_for(names, start_text, end_text):
    interval = local_window_interval(start_text, end_text)
    return {name: [interval] for name in names}


def parse_local_time(value):
    parsed = time.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError("Daily local times should not include a timezone offset.")
    return parsed.replace(second=0, microsecond=0)


def daily_windows_for(names, start_date_text, days, start_time_text, end_time_text):
    start_date = parse_local_date(start_date_text)
    start_time = parse_local_time(start_time_text)
    end_time = parse_local_time(end_time_text)
    if end_time <= start_time:
        raise ValueError("--daily-local-end-time must be later than --daily-local-start-time.")
    if days <= 0:
        raise ValueError("--daily-days must be positive.")

    intervals = []
    for offset in range(days):
        current_date = start_date + timedelta(days=offset)
        start = datetime.combine(current_date, start_time).isoformat()
        end = datetime.combine(current_date, end_time).isoformat()
        intervals.append((start, end))
    return {name: intervals for name in names}


def fetch_existing_postcodes(run_id, windows):
    client = bigquery.Client(project=config.PROJECT_ID, location=config.LOCATION)
    query = f"""
    SELECT planned_window, postcode
    FROM `{config.table_id("job_manifest")}`
    WHERE run_id = @run_id
      AND planned_window IN UNNEST(@windows)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
            bigquery.ArrayQueryParameter("windows", "STRING", list(windows)),
        ]
    )
    existing = {window: set() for window in windows}
    for row in client.query(query, job_config=job_config, location=config.LOCATION).result():
        existing.setdefault(row["planned_window"], set()).add(row["postcode"])
    return existing


def fetch_existing_job_ids(run_id):
    client = bigquery.Client(project=config.PROJECT_ID, location=config.LOCATION)
    query = f"""
    SELECT job_id
    FROM `{config.table_id("job_manifest")}`
    WHERE run_id = @run_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]
    )
    return {
        row["job_id"]
        for row in client.query(query, job_config=job_config, location=config.LOCATION).result()
    }


def build_payloads(
    postcodes,
    windows,
    windows_config,
    provider,
    run_id,
    max_attempts,
    queue_id,
    schedule_now=False,
):
    created_at = utc_now().isoformat()
    payloads = []
    manifest_rows = []
    for planned_window in windows:
        if schedule_now:
            scheduled_values = immediate_schedule(len(postcodes))
        else:
            scheduled_values = schedule_across_intervals(windows_config[planned_window], len(postcodes))
        for postcode, scheduled_at in zip(postcodes, scheduled_values):
            job_id = stable_job_id(run_id, provider, planned_window, postcode)
            payload = {
                "job_id": job_id,
                "run_id": run_id,
                "provider": provider,
                "postcode": postcode,
                "planned_window": planned_window,
                "scheduled_at": scheduled_at.isoformat(),
            }
            payloads.append(payload)
            manifest_rows.append(
                {
                    **payload,
                    "created_at": created_at,
                    "task_name": task_name_for_job(queue_id, job_id),
                    "status": "pending",
                    "attempt_count": 0,
                    "max_attempts": max_attempts,
                    "started_at": None,
                    "finished_at": None,
                    "raw_uri": None,
                    "processed_rows": None,
                    "error": None,
                }
            )
    return payloads, manifest_rows


def build_payloads_skipping_existing(
    postcodes,
    windows,
    windows_config,
    provider,
    run_id,
    max_attempts,
    queue_id,
    existing_by_window,
    schedule_now=False,
):
    created_at = utc_now().isoformat()
    payloads = []
    manifest_rows = []
    skipped = {}
    for planned_window in windows:
        existing = existing_by_window.get(planned_window, set())
        missing_postcodes = [postcode for postcode in postcodes if postcode not in existing]
        skipped[planned_window] = len(postcodes) - len(missing_postcodes)
        if schedule_now:
            scheduled_values = immediate_schedule(len(missing_postcodes))
        else:
            scheduled_values = schedule_across_intervals(
                windows_config[planned_window],
                len(missing_postcodes),
            )
        for postcode, scheduled_at in zip(missing_postcodes, scheduled_values):
            job_id = stable_job_id(run_id, provider, planned_window, postcode)
            payload = {
                "job_id": job_id,
                "run_id": run_id,
                "provider": provider,
                "postcode": postcode,
                "planned_window": planned_window,
                "scheduled_at": scheduled_at.isoformat(),
            }
            payloads.append(payload)
            manifest_rows.append(
                {
                    **payload,
                    "created_at": created_at,
                    "task_name": task_name_for_job(queue_id, job_id),
                    "status": "pending",
                    "attempt_count": 0,
                    "max_attempts": max_attempts,
                    "started_at": None,
                    "finished_at": None,
                    "raw_uri": None,
                    "processed_rows": None,
                    "error": None,
                }
            )
    return payloads, manifest_rows, skipped


def ensure_queue(queue_id, max_dispatches_per_second, max_concurrent_dispatches):
    client = tasks_v2.CloudTasksClient()
    parent = client.common_location_path(config.PROJECT_ID, config.LOCATION)
    queue_path = client.queue_path(config.PROJECT_ID, config.LOCATION, queue_id)
    queue = tasks_v2.Queue(
        name=queue_path,
        rate_limits=tasks_v2.RateLimits(
            max_dispatches_per_second=max_dispatches_per_second,
            max_concurrent_dispatches=max_concurrent_dispatches,
        ),
        retry_config=tasks_v2.RetryConfig(
            max_attempts=3,
            min_backoff=timedelta(seconds=30),
            max_backoff=timedelta(minutes=10),
            max_doublings=5,
        ),
    )
    try:
        return client.create_queue(parent=parent, queue=queue)
    except AlreadyExists:
        return client.update_queue(queue=queue)


def create_tasks(payloads, queue_id, service_url, oidc_service_account_email, dry_run=False):
    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(config.PROJECT_ID, config.LOCATION, queue_id)
    created = []
    for payload in payloads:
        scheduled_at = datetime.fromisoformat(payload["scheduled_at"])
        schedule_time = timestamp_pb2.Timestamp()
        schedule_time.FromDatetime(scheduled_at)
        task = tasks_v2.Task(
            name=client.task_path(
                config.PROJECT_ID,
                config.LOCATION,
                queue_id,
                task_id_for_job(payload["job_id"]),
            ),
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=f"{service_url.rstrip('/')}/tasks/justeat",
                headers={"Content-Type": "application/json"},
                body=json.dumps(payload).encode("utf-8"),
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=oidc_service_account_email,
                ),
            ),
            schedule_time=schedule_time,
        )
        if dry_run:
            created.append({"job_id": payload["job_id"], "task_name": None})
            continue
        try:
            response = client.create_task(parent=parent, task=task)
            task_name = response.name
        except AlreadyExists:
            task_name = task.name
        created.append({"job_id": payload["job_id"], "task_name": task_name})
    return created


def insert_manifest(rows):
    if not rows:
        return
    client = bigquery.Client(project=config.PROJECT_ID, location=config.LOCATION)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    load_job = client.load_table_from_json(
        rows,
        config.table_id("job_manifest"),
        job_config=job_config,
        location=config.LOCATION,
    )
    load_job.result()


def parse_args():
    parser = argparse.ArgumentParser(description="Create Cloud Tasks for scrape jobs.")
    parser.add_argument("--postcode-file", default="data/input/postcodes_full.csv")
    parser.add_argument("--limit-postcodes", type=int)
    parser.add_argument("--windows", nargs="+", default=list(DEFAULT_WINDOW_NAMES))
    parser.add_argument("--weeks", type=int, default=2)
    parser.add_argument("--start-date", help="Local Europe/London date, e.g. 2026-05-13.")
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--provider", default="just_eat")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--queue-id", default="delivery-scrape-tasks")
    parser.add_argument("--service-url", required=True)
    parser.add_argument(
        "--oidc-service-account-email",
        default=(
            "scheduler-runner@delivery-availability-research.iam.gserviceaccount.com"
        ),
    )
    parser.add_argument("--max-dispatches-per-second", type=float, default=1.1)
    parser.add_argument("--max-concurrent-dispatches", type=int, default=2)
    parser.add_argument(
        "--skip-queue-setup",
        action="store_true",
        help="Use an existing Cloud Tasks queue without creating or updating queue settings.",
    )
    parser.add_argument(
        "--schedule-now",
        action="store_true",
        help="Smoke-test mode: schedule tasks immediately while keeping planned_window labels.",
    )
    parser.add_argument(
        "--custom-local-start",
        help="Override selected windows with a local Europe/London start, e.g. 2026-05-13T16:00:00.",
    )
    parser.add_argument(
        "--custom-local-end",
        help="Override selected windows with a local Europe/London end, e.g. 2026-05-13T16:05:00.",
    )
    parser.add_argument(
        "--daily-start-date",
        help="Build repeated daily windows from this local Europe/London date, e.g. 2026-05-16.",
    )
    parser.add_argument(
        "--daily-days",
        type=int,
        default=0,
        help="Number of daily windows to build when using --daily-start-date.",
    )
    parser.add_argument(
        "--daily-local-start-time",
        help="Daily local start time, e.g. 12:00.",
    )
    parser.add_argument(
        "--daily-local-end-time",
        help="Daily local end time, e.g. 20:00.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip postcodes already present in job_manifest for each planned window.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if bool(args.custom_local_start) != bool(args.custom_local_end):
        raise ValueError("Use --custom-local-start and --custom-local-end together.")
    daily_args = [
        args.daily_start_date,
        args.daily_days,
        args.daily_local_start_time,
        args.daily_local_end_time,
    ]
    using_daily_windows = any(daily_args)
    if using_daily_windows and not all(daily_args):
        raise ValueError(
            "Use --daily-start-date, --daily-days, --daily-local-start-time, "
            "and --daily-local-end-time together."
        )
    if using_daily_windows and args.custom_local_start:
        raise ValueError("Use either custom local window or daily windows, not both.")

    if using_daily_windows:
        windows_config = daily_windows_for(
            args.windows,
            args.daily_start_date,
            args.daily_days,
            args.daily_local_start_time,
            args.daily_local_end_time,
        )
    elif args.custom_local_start:
        windows_config = custom_windows_for(
            args.windows,
            args.custom_local_start,
            args.custom_local_end,
        )
    else:
        windows_config = build_windows(weeks=args.weeks, start_date=args.start_date)
    unknown = sorted(set(args.windows) - set(windows_config))
    if unknown:
        raise ValueError(f"Unknown windows: {', '.join(unknown)}")
    empty = [window for window in args.windows if not windows_config[window]]
    if empty:
        raise ValueError(f"No intervals generated for: {', '.join(empty)}")

    postcodes = read_postcodes(args.postcode_file, limit=args.limit_postcodes)
    skipped = {}
    if args.skip_existing:
        existing = fetch_existing_postcodes(args.run_id, args.windows)
        payloads, manifest_rows, skipped = build_payloads_skipping_existing(
            postcodes,
            args.windows,
            windows_config,
            args.provider,
            args.run_id,
            args.max_attempts,
            args.queue_id,
            existing,
            schedule_now=args.schedule_now,
        )
    else:
        payloads, manifest_rows = build_payloads(
            postcodes,
            args.windows,
            windows_config,
            args.provider,
            args.run_id,
            args.max_attempts,
            args.queue_id,
            schedule_now=args.schedule_now,
        )
    existing_job_ids = fetch_existing_job_ids(args.run_id) if not args.dry_run else set()
    new_manifest_rows = [row for row in manifest_rows if row["job_id"] not in existing_job_ids]
    print(f"Postcodes: {len(postcodes)}")
    print(f"Windows: {', '.join(args.windows)}")
    print(f"Run ID: {args.run_id}")
    if args.custom_local_start:
        print(f"Schedule: custom local window {args.custom_local_start} -> {args.custom_local_end}")
    elif using_daily_windows:
        print(
            "Schedule: daily local windows "
            f"{args.daily_start_date} + {args.daily_days} day(s), "
            f"{args.daily_local_start_time} -> {args.daily_local_end_time}"
        )
    else:
        print(f"Schedule: {args.weeks} week(s) from {parse_local_date(args.start_date).isoformat()}")
    print(f"Tasks to create: {len(payloads)}")
    print(f"New manifest rows: {len(new_manifest_rows)}")
    for window in args.windows:
        items = [p for p in payloads if p["planned_window"] == window]
        skipped_text = f" | skipped_existing={skipped.get(window, 0)}" if args.skip_existing else ""
        if items:
            print(
                f"  {window}: {len(items)} | {items[0]['scheduled_at']} -> "
                f"{items[-1]['scheduled_at']}{skipped_text}"
            )
        else:
            print(f"  {window}: 0 tasks to create{skipped_text}")

    if args.dry_run:
        print("Dry run only; no queue/tasks/manifest created.")
        return
    if not payloads:
        print("No missing tasks to create.")
        return

    insert_manifest(new_manifest_rows)
    if not args.skip_queue_setup:
        ensure_queue(
            args.queue_id,
            max_dispatches_per_second=args.max_dispatches_per_second,
            max_concurrent_dispatches=args.max_concurrent_dispatches,
        )
    created_tasks = create_tasks(
        payloads,
        queue_id=args.queue_id,
        service_url=args.service_url,
        oidc_service_account_email=args.oidc_service_account_email,
        dry_run=False,
    )
    print(f"Created {len(created_tasks)} Cloud Tasks in queue {args.queue_id}")
    print(f"Inserted {len(new_manifest_rows)} rows into {config.table_id('job_manifest')}")


if __name__ == "__main__":
    main()
