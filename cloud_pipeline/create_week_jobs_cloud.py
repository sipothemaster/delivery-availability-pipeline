import argparse
import csv
import re
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from google.cloud import bigquery

from cloud_pipeline import config


LONDON = ZoneInfo("Europe/London")
WINDOW_END_GUARD_SECONDS = 60


WINDOW_RULES = {
    "weekday_morning": {"weekdays": {1, 2, 3}, "start": time(7, 0), "end": time(10, 0)},
    "weekday_evening": {"weekdays": {1, 2, 3}, "start": time(17, 0), "end": time(21, 0)},
    "weekend_morning": {"weekdays": {5, 6}, "start": time(7, 0), "end": time(10, 0)},
    "weekend_evening": {"weekdays": {4, 5, 6}, "start": time(17, 0), "end": time(21, 0)},
}

DEFAULT_WINDOW_NAMES = tuple(WINDOW_RULES)


def clean_postcode(postcode):
    return re.sub(r"\s+", "", str(postcode)).lower()


def utc_now():
    return datetime.now(LONDON).astimezone(ZoneInfo("UTC")).replace(microsecond=0)


def parse_local_date(value):
    if value is None:
        return datetime.now(LONDON).date()
    return date.fromisoformat(value)


def build_windows(weeks=1, start_date=None):
    first_date = parse_local_date(start_date)
    days = max(1, int(weeks) * 7)
    windows = {name: [] for name in WINDOW_RULES}
    for offset in range(days):
        current_date = first_date + timedelta(days=offset)
        for name, rule in WINDOW_RULES.items():
            if current_date.weekday() not in rule["weekdays"]:
                continue
            start = datetime.combine(current_date, rule["start"]).isoformat()
            end = datetime.combine(current_date, rule["end"]).isoformat()
            windows[name].append((start, end))
    return windows


def local_interval(start_text, end_text):
    start = datetime.fromisoformat(start_text).replace(tzinfo=LONDON)
    end = datetime.fromisoformat(end_text).replace(tzinfo=LONDON)
    return start, end


def read_postcodes(path, limit=None):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if "postcode" not in reader.fieldnames:
            raise ValueError("Input CSV must contain a 'postcode' column.")
        postcodes = [clean_postcode(row["postcode"]) for row in reader if row.get("postcode")]
    if limit:
        postcodes = postcodes[:limit]
    return postcodes


def schedule_across_intervals_with_bounds(
    intervals,
    count,
    end_guard_seconds=WINDOW_END_GUARD_SECONDS,
):
    if count <= 0:
        return []
    if end_guard_seconds < 0:
        raise ValueError("Window end guard must not be negative.")

    parsed = []
    for start_text, end_text in intervals:
        start, end = local_interval(start_text, end_text)
        guarded_end = end - timedelta(seconds=end_guard_seconds)
        if guarded_end <= start:
            raise ValueError("Window intervals must be longer than the end guard.")
        parsed.append((start, guarded_end, end))

    durations = [(guarded_end - start).total_seconds() for start, guarded_end, _ in parsed]
    total_seconds = sum(durations)
    if total_seconds <= 0:
        raise ValueError("Window intervals must have positive duration.")
    if count == 1:
        start, _, end = parsed[0]
        return [
            (
                start.astimezone(ZoneInfo("UTC")).replace(microsecond=0),
                end.astimezone(ZoneInfo("UTC")).replace(microsecond=0),
            )
        ]

    scheduled = []
    step_seconds = total_seconds / count
    for index in range(count):
        offset = index * step_seconds
        remaining = offset
        for (start, _, interval_end), duration in zip(parsed, durations):
            if remaining < duration:
                scheduled_at = start + timedelta(seconds=remaining)
                scheduled.append(
                    (
                        scheduled_at.astimezone(ZoneInfo("UTC")).replace(microsecond=0),
                        interval_end.astimezone(ZoneInfo("UTC")).replace(microsecond=0),
                    )
                )
                break
            remaining -= duration
    return scheduled


def schedule_across_intervals(
    intervals,
    count,
    end_guard_seconds=WINDOW_END_GUARD_SECONDS,
):
    return [
        scheduled_at
        for scheduled_at, _ in schedule_across_intervals_with_bounds(
            intervals,
            count,
            end_guard_seconds=end_guard_seconds,
        )
    ]


def build_rows(postcodes, window_names, windows_config, provider, max_attempts):
    created_at = utc_now().isoformat()
    rows = []
    for planned_window in window_names:
        scheduled_values = schedule_across_intervals(windows_config[planned_window], len(postcodes))
        for postcode, scheduled_at in zip(postcodes, scheduled_values):
            rows.append(
                {
                    "job_id": str(uuid.uuid4()),
                    "provider": provider,
                    "postcode": postcode,
                    "planned_window": planned_window,
                    "scheduled_at": scheduled_at.isoformat(),
                    "status": "pending",
                    "attempt_count": 0,
                    "max_attempts": max_attempts,
                    "created_at": created_at,
                    "started_at": None,
                    "finished_at": None,
                    "raw_uri": None,
                    "processed_rows": None,
                    "error": None,
                }
            )
    return rows


def insert_jobs(rows):
    client = bigquery.Client(project=config.PROJECT_ID, location=config.LOCATION)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    load_job = client.load_table_from_json(
        rows,
        config.table_id(config.JOBS_TABLE),
        job_config=job_config,
        location=config.LOCATION,
    )
    load_job.result()


def parse_args():
    parser = argparse.ArgumentParser(description="Create this week's cloud scrape jobs.")
    parser.add_argument("--postcode-file", default="data/input/postcodes_full.csv")
    parser.add_argument("--limit-postcodes", type=int)
    parser.add_argument("--windows", nargs="+", default=list(DEFAULT_WINDOW_NAMES))
    parser.add_argument("--weeks", type=int, default=2)
    parser.add_argument("--start-date", help="Local Europe/London date, e.g. 2026-05-13.")
    parser.add_argument("--provider", default="just_eat")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    windows_config = build_windows(weeks=args.weeks, start_date=args.start_date)
    unknown = sorted(set(args.windows) - set(windows_config))
    if unknown:
        raise ValueError(f"Unknown windows: {', '.join(unknown)}")
    empty = [window for window in args.windows if not windows_config[window]]
    if empty:
        raise ValueError(f"No intervals generated for: {', '.join(empty)}")

    postcodes = read_postcodes(Path(args.postcode_file), limit=args.limit_postcodes)
    rows = build_rows(postcodes, args.windows, windows_config, args.provider, args.max_attempts)

    print(f"Postcodes: {len(postcodes)}")
    print(f"Windows: {', '.join(args.windows)}")
    print(f"Schedule: {args.weeks} week(s) from {parse_local_date(args.start_date).isoformat()}")
    print(f"Jobs to create: {len(rows)}")
    for window in args.windows:
        window_rows = [row for row in rows if row["planned_window"] == window]
        print(
            f"  {window}: {len(window_rows)} jobs | "
            f"{window_rows[0]['scheduled_at']} -> {window_rows[-1]['scheduled_at']}"
        )

    if args.dry_run:
        print("Dry run only; no jobs inserted.")
        return

    insert_jobs(rows)
    print(f"Inserted {len(rows)} jobs into {config.table_id(config.JOBS_TABLE)}")


if __name__ == "__main__":
    main()
