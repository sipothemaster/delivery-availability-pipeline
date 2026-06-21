import argparse
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from google.api_core.exceptions import AlreadyExists
from google.cloud import bigquery, tasks_v2
from google.protobuf import timestamp_pb2

from cloud_pipeline import config
from cloud_pipeline.setup_tables import ensure_dataset, ensure_table
from cloud_pipeline.schema import (
    MENU_MANIFEST_RESULTS_SCHEMA,
    MENU_MANIFEST_TASKS_SCHEMA,
    RESTAURANT_OPENING_TIMES_SCHEMA,
)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def stable_task_id(run_id, restaurant_id, slug):
    key = f"{run_id}|{restaurant_id}|{slug}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def task_name_for_task(queue_id, task_id):
    return (
        f"projects/{config.PROJECT_ID}/locations/{config.LOCATION}/"
        f"queues/{queue_id}/tasks/menu-manifest-{task_id}"
    )


def ensure_menu_tables(client):
    ensure_dataset(client)
    ensure_table(
        client,
        "menu_manifest_tasks",
        MENU_MANIFEST_TASKS_SCHEMA,
        partition_field="scheduled_at",
    )
    ensure_table(
        client,
        "menu_manifest_results",
        MENU_MANIFEST_RESULTS_SCHEMA,
        partition_field="fetched_at",
    )
    ensure_table(
        client,
        "restaurant_opening_times",
        RESTAURANT_OPENING_TIMES_SCHEMA,
        partition_field="fetched_at",
    )


def sample_restaurants(client, limit, seed):
    query = f"""
    SELECT
      restaurant_id,
      restaurant_name,
      restaurant_unique_name,
      restaurant_url,
      cuisine_names
    FROM `{config.PROJECT_ID}.{config.DATASET_ID}.restaurant_profile`
    WHERE restaurant_unique_name IS NOT NULL
      AND restaurant_unique_name != ''
    ORDER BY FARM_FINGERPRINT(CONCAT(CAST(restaurant_id AS STRING), @seed))
    LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("seed", "STRING", seed),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
    )
    return [
        {
            "restaurant_id": str(row["restaurant_id"]),
            "restaurant_name": row["restaurant_name"],
            "restaurant_unique_name": row["restaurant_unique_name"],
            "restaurant_url": row["restaurant_url"],
            "cuisine_names": row["cuisine_names"],
        }
        for row in client.query(query, job_config=job_config, location=config.LOCATION).result()
    ]


def build_payloads(restaurants, run_id):
    now = utc_now()
    payloads = []
    for index, restaurant in enumerate(restaurants):
        scheduled_at = now + timedelta(seconds=index)
        task_id = stable_task_id(
            run_id,
            restaurant["restaurant_id"],
            restaurant["restaurant_unique_name"],
        )
        payloads.append(
            {
                "task_id": task_id,
                "run_id": run_id,
                **restaurant,
                "scheduled_at": scheduled_at.isoformat(),
                "created_at": now.isoformat(),
            }
        )
    return payloads


def insert_task_manifest(client, payloads):
    rows = [
        {
            "task_id": payload["task_id"],
            "run_id": payload["run_id"],
            "restaurant_id": payload["restaurant_id"],
            "restaurant_name": payload.get("restaurant_name"),
            "restaurant_unique_name": payload["restaurant_unique_name"],
            "restaurant_url": payload.get("restaurant_url"),
            "cuisine_names": payload.get("cuisine_names"),
            "scheduled_at": payload["scheduled_at"],
            "created_at": payload["created_at"],
            "task_name": payload.get("task_name"),
        }
        for payload in payloads
    ]

    table = config.table_id("menu_manifest_tasks")
    for start in range(0, len(rows), 500):
        chunk = rows[start : start + 500]
        errors = client.insert_rows_json(table, chunk)
        if errors:
            raise RuntimeError(f"BigQuery menu_manifest_tasks insert errors: {errors[:3]}")
        if (start + len(chunk)) % 5000 == 0:
            print(f"Inserted {start + len(chunk)}/{len(rows)} task manifest rows", flush=True)


def create_queue(queue_id, max_dispatches_per_second, max_concurrent_dispatches):
    client = tasks_v2.CloudTasksClient()
    parent = f"projects/{config.PROJECT_ID}/locations/{config.LOCATION}"
    queue = tasks_v2.Queue(
        name=f"{parent}/queues/{queue_id}",
        rate_limits=tasks_v2.RateLimits(
            max_dispatches_per_second=max_dispatches_per_second,
            max_concurrent_dispatches=max_concurrent_dispatches,
        ),
        retry_config=tasks_v2.RetryConfig(
            max_attempts=3,
            min_backoff={"seconds": 30},
            max_backoff={"seconds": 600},
        ),
    )
    try:
        return client.create_queue(parent=parent, queue=queue)
    except AlreadyExists:
        return client.get_queue(name=queue.name)


def create_task(client, parent, queue_id, service_url, oidc_service_account_email, payload):
    endpoint = service_url.rstrip("/") + "/tasks/justeat-menu-manifest"
    timestamp = timestamp_pb2.Timestamp()
    scheduled_at = datetime.fromisoformat(payload["scheduled_at"])
    timestamp.FromDatetime(scheduled_at)
    task = tasks_v2.Task(
        name=task_name_for_task(queue_id, payload["task_id"]),
        schedule_time=timestamp,
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=endpoint,
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload).encode("utf-8"),
            oidc_token=tasks_v2.OidcToken(
                service_account_email=oidc_service_account_email,
                audience=service_url.rstrip("/"),
            ),
        ),
    )
    try:
        response = client.create_task(parent=parent, task=task)
        return response.name
    except AlreadyExists:
        return task.name


def create_tasks(queue_id, service_url, oidc_service_account_email, payloads, workers):
    client = tasks_v2.CloudTasksClient()
    parent = f"projects/{config.PROJECT_ID}/locations/{config.LOCATION}/queues/{queue_id}"
    created = []
    created_count = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for batch_start in range(0, len(payloads), 5000):
            batch = payloads[batch_start : batch_start + 5000]
            futures = [
                executor.submit(
                    create_task,
                    client,
                    parent,
                    queue_id,
                    service_url,
                    oidc_service_account_email,
                    payload,
                )
                for payload in batch
            ]
            for future in as_completed(futures):
                created.append(future.result())
                created_count += 1
                if created_count % 1000 == 0:
                    print(f"Created {created_count}/{len(payloads)} tasks", flush=True)
    return created


def parse_args():
    parser = argparse.ArgumentParser(description="Create Just Eat menu manifest probe tasks.")
    parser.add_argument("--run-id", default=f"menu-manifest-probe-{utc_now().date().isoformat()}")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--seed", default="menu-manifest-cloud-probe-20260621")
    parser.add_argument("--queue-id", default="justeat-menu-manifest-probe")
    parser.add_argument("--service-url", required=True)
    parser.add_argument("--oidc-service-account-email", required=True)
    parser.add_argument("--max-dispatches-per-second", type=float, default=1.0)
    parser.add_argument("--max-concurrent-dispatches", type=int, default=4)
    parser.add_argument("--create-task-workers", type=int, default=32)
    parser.add_argument("--skip-queue-create", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    bq_client = bigquery.Client(project=config.PROJECT_ID, location=config.LOCATION)
    ensure_menu_tables(bq_client)
    restaurants = sample_restaurants(bq_client, args.limit, args.seed)
    payloads = build_payloads(restaurants, args.run_id)
    for payload in payloads:
        payload["task_name"] = task_name_for_task(args.queue_id, payload["task_id"])

    print(f"Run ID: {args.run_id}")
    print(f"Restaurants: {len(payloads)}")
    print(f"Queue: {args.queue_id}")
    print(f"Service URL: {args.service_url}")
    print(f"First scheduled: {payloads[0]['scheduled_at'] if payloads else ''}")
    print(f"Last scheduled: {payloads[-1]['scheduled_at'] if payloads else ''}")
    if args.dry_run:
        return

    if not args.skip_queue_create:
        create_queue(args.queue_id, args.max_dispatches_per_second, args.max_concurrent_dispatches)
    insert_task_manifest(bq_client, payloads)
    created = create_tasks(
        args.queue_id,
        args.service_url,
        args.oidc_service_account_email,
        payloads,
        args.create_task_workers,
    )
    print(f"Created tasks: {len(created)}")


if __name__ == "__main__":
    main()
