import os
import sys

from cloud_pipeline import create_tasks_cloud


def add_arg(args, name, value=None):
    args.append(name)
    if value is not None:
        args.append(str(value))


def main():
    args = []
    add_arg(args, "--postcode-file", os.environ["POSTCODE_FILE"])
    add_arg(args, "--service-url", os.environ["TASK_WORKER_SERVICE_URL"])
    add_arg(
        args,
        "--oidc-service-account-email",
        os.environ["OIDC_SERVICE_ACCOUNT_EMAIL"],
    )
    add_arg(args, "--queue-id", os.getenv("TASK_QUEUE_ID", "delivery-scrape-tasks"))
    add_arg(args, "--max-dispatches-per-second", os.getenv("MAX_DISPATCHES_PER_SECOND", "0.8"))
    add_arg(args, "--max-concurrent-dispatches", os.getenv("MAX_CONCURRENT_DISPATCHES", "2"))
    add_arg(args, "--weeks", os.getenv("SCHEDULE_WEEKS", "2"))

    run_id = os.getenv("RUN_ID")
    if run_id:
        add_arg(args, "--run-id", run_id)

    windows = os.getenv("WINDOWS")
    if windows:
        add_arg(args, "--windows")
        args.extend(item for item in windows.replace(",", " ").split() if item)

    max_attempts = os.getenv("MAX_ATTEMPTS")
    if max_attempts:
        add_arg(args, "--max-attempts", max_attempts)

    start_date = os.getenv("START_DATE")
    if start_date:
        add_arg(args, "--start-date", start_date)

    custom_start = os.getenv("CUSTOM_LOCAL_START")
    custom_end = os.getenv("CUSTOM_LOCAL_END")
    if custom_start or custom_end:
        add_arg(args, "--custom-local-start", custom_start)
        add_arg(args, "--custom-local-end", custom_end)

    daily_start_date = os.getenv("DAILY_START_DATE")
    daily_days = os.getenv("DAILY_DAYS")
    daily_start_time = os.getenv("DAILY_LOCAL_START_TIME")
    daily_end_time = os.getenv("DAILY_LOCAL_END_TIME")
    if daily_start_date or daily_days or daily_start_time or daily_end_time:
        add_arg(args, "--daily-start-date", daily_start_date)
        add_arg(args, "--daily-days", daily_days)
        add_arg(args, "--daily-local-start-time", daily_start_time)
        add_arg(args, "--daily-local-end-time", daily_end_time)

    window_intervals_json = os.getenv("WINDOW_INTERVALS_JSON")
    if window_intervals_json:
        add_arg(args, "--window-intervals-json", window_intervals_json)

    window_intervals_file = os.getenv("WINDOW_INTERVALS_FILE")
    if window_intervals_file:
        add_arg(args, "--window-intervals-file", window_intervals_file)

    limit_postcodes = os.getenv("LIMIT_POSTCODES")
    if limit_postcodes:
        add_arg(args, "--limit-postcodes", limit_postcodes)

    if os.getenv("SKIP_EXISTING", "true").lower() in {"1", "true", "yes", "y"}:
        add_arg(args, "--skip-existing")

    if os.getenv("SCHEDULE_NOW", "false").lower() in {"1", "true", "yes", "y"}:
        add_arg(args, "--schedule-now")

    if os.getenv("SKIP_QUEUE_SETUP", "false").lower() in {"1", "true", "yes", "y"}:
        add_arg(args, "--skip-queue-setup")

    if os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes", "y"}:
        add_arg(args, "--dry-run")

    create_task_workers = os.getenv("CREATE_TASK_WORKERS")
    if create_task_workers:
        add_arg(args, "--create-task-workers", create_task_workers)

    sys.argv = ["create_tasks_cloud", *args]
    create_tasks_cloud.main()


if __name__ == "__main__":
    main()
