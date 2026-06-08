import gzip
import json
import os
import time
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import bigquery, storage

from cloud_pipeline import config
from cloud_pipeline.justeat_api import (
    JustEatAPIError,
    api_restaurant_to_row,
    clean_postcode,
    fetch_listing_api,
)


app = Flask(__name__)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def utc_now_precise():
    return datetime.now(timezone.utc)


def isoformat_or_none(value):
    return value.isoformat() if value else None


def millis_to_datetime(value):
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y"}


def load_ban_state(storage_client, ban_key):
    bucket = storage_client.bucket(config.BUCKET_NAME)
    blob = bucket.blob(f"rate_limiter/{ban_key}_ban.json")
    try:
        state = json.loads(blob.download_as_text(encoding="utf-8") or "{}")
    except NotFound:
        return None
    ban_until_ms = state.get("ban_until_ms")
    try:
        return int(ban_until_ms) if ban_until_ms is not None else None
    except (TypeError, ValueError):
        return None


def set_ban_state(storage_client, ban_key, ban_seconds, reason):
    ban_until_ms = int(time.time() * 1000) + int(ban_seconds * 1000)
    bucket = storage_client.bucket(config.BUCKET_NAME)
    blob = bucket.blob(f"rate_limiter/{ban_key}_ban.json")
    blob.upload_from_string(
        json.dumps(
            {
                "ban_key": ban_key,
                "ban_until_ms": ban_until_ms,
                "ban_seconds": int(ban_seconds),
                "reason": reason,
                "updated_at": utc_now_precise().isoformat(),
            },
            sort_keys=True,
        ),
        content_type="application/json",
    )
    return ban_until_ms


def active_ban_result(storage_client, ban_key):
    ban_until_ms = load_ban_state(storage_client, ban_key)
    now_ms = int(time.time() * 1000)
    if ban_until_ms and ban_until_ms > now_ms:
        return ban_until_ms, ban_until_ms - now_ms
    return None, 0


def parse_open_restaurants(data):
    restaurants = data.get("restaurants") or []
    grocery_ids = set(
        str(restaurant_id)
        for restaurant_id in (
            (data.get("filters") or {}).get("groceries", {}).get("restaurantIds") or []
        )
    )
    open_restaurants = [
        restaurant
        for restaurant in restaurants
        if restaurant.get("isDelivery")
        and restaurant.get("isOpenNowForDelivery")
        and not restaurant.get("isTemporarilyOffline")
    ]
    return [api_restaurant_to_row(restaurant, grocery_ids) for restaurant in open_restaurants]


def insert_event(client, payload, event_type, message=None, raw_uri=None, processed_rows=None):
    row = {
        "event_id": str(uuid.uuid4()),
        "job_id": payload["job_id"],
        "provider": payload["provider"],
        "postcode": payload["postcode"],
        "planned_window": payload["planned_window"],
        "event_type": event_type,
        "event_time": utc_now().isoformat(),
        "message": message,
        "raw_uri": raw_uri,
        "processed_rows": processed_rows,
    }
    errors = client.insert_rows_json(config.table_id("job_events"), [row])
    if errors:
        print(f"BigQuery job_events insert errors: {errors}", flush=True)


def insert_diagnostic(client, payload, diagnostic):
    row = {
        "diagnostic_id": str(uuid.uuid4()),
        "job_id": payload["job_id"],
        "run_id": payload.get("run_id"),
        "provider": payload["provider"],
        "postcode": payload["postcode"],
        "planned_window": payload["planned_window"],
        "diagnostic_time": utc_now_precise().isoformat(),
        "worker_received_at": isoformat_or_none(diagnostic.get("worker_received_at")),
        "worker_started_at": isoformat_or_none(diagnostic.get("worker_started_at")),
        "limiter_enabled": diagnostic.get("limiter_enabled"),
        "limiter_key": diagnostic.get("limiter_key"),
        "limiter_acquire_started_at": isoformat_or_none(
            diagnostic.get("limiter_acquire_started_at")
        ),
        "limiter_acquire_finished_at": isoformat_or_none(
            diagnostic.get("limiter_acquire_finished_at")
        ),
        "limiter_attempts": diagnostic.get("limiter_attempts"),
        "limiter_spacing_ms": diagnostic.get("limiter_spacing_ms"),
        "limiter_previous_next_allowed_ms": diagnostic.get(
            "limiter_previous_next_allowed_ms"
        ),
        "limiter_token_reserved_for_ms": diagnostic.get("limiter_token_reserved_for_ms"),
        "limiter_token_reserved_for": isoformat_or_none(
            millis_to_datetime(diagnostic.get("limiter_token_reserved_for_ms"))
        ),
        "limiter_wait_ms": diagnostic.get("limiter_wait_ms"),
        "api_request_started_at": isoformat_or_none(diagnostic.get("api_request_started_at")),
        "api_request_finished_at": isoformat_or_none(
            diagnostic.get("api_request_finished_at")
        ),
        "api_latency_ms": diagnostic.get("api_latency_ms"),
        "api_url": diagnostic.get("api_url"),
        "http_status": diagnostic.get("http_status"),
        "outcome": diagnostic.get("outcome"),
        "processed_rows": diagnostic.get("processed_rows"),
        "raw_uri": diagnostic.get("raw_uri"),
        "error": diagnostic.get("error"),
    }
    errors = client.insert_rows_json(config.table_id("job_diagnostics"), [row])
    if errors:
        print(f"BigQuery job_diagnostics insert errors: {errors}", flush=True)


def acquire_global_rate_token(storage_client, limiter_key, spacing_ms):
    acquire_started_at = utc_now_precise()
    start_guard_ms = int(os.getenv("JUSTEAT_RATE_LIMIT_START_GUARD_MS", "1000"))
    bucket = storage_client.bucket(config.BUCKET_NAME)
    state_blob = bucket.blob(f"rate_limiter/{limiter_key}_state.json")
    lock_blob = bucket.blob(f"rate_limiter/{limiter_key}_lock.json")
    attempts = 0
    previous_start_ms = None
    lock_generation = None

    while True:
        attempts += 1
        now_ms = int(time.time() * 1000)
        try:
            lock_blob.upload_from_string(
                json.dumps({"created_at_ms": now_ms, "limiter_key": limiter_key}),
                content_type="application/json",
                if_generation_match=0,
            )
            lock_blob.reload()
            lock_generation = int(lock_blob.generation)
            break
        except PreconditionFailed:
            try:
                lock_blob.reload()
                lock_state = json.loads(lock_blob.download_as_text(encoding="utf-8") or "{}")
                lock_created_at_ms = int(lock_state.get("created_at_ms") or 0)
                if lock_created_at_ms and now_ms - lock_created_at_ms > 120000:
                    lock_blob.delete(if_generation_match=int(lock_blob.generation))
            except (NotFound, PreconditionFailed, ValueError, TypeError):
                pass
            time.sleep(min(0.05 * attempts, 0.5))

    result = None
    try:
        state = {}
        try:
            state_blob.reload()
            state = json.loads(state_blob.download_as_text(encoding="utf-8") or "{}")
        except NotFound:
            state = {}

        previous_start_ms = state.get("last_api_start_ms")
        try:
            previous_start_ms = (
                int(previous_start_ms)
                if previous_start_ms is not None
                else None
            )
        except (TypeError, ValueError):
            previous_start_ms = None

        now_ms = int(time.time() * 1000)
        token_reserved_for_ms = max(
            now_ms + start_guard_ms,
            (previous_start_ms or 0) + int(spacing_ms),
        )
        state_blob.upload_from_string(
            json.dumps(
                {
                    "limiter_key": limiter_key,
                    "last_api_start_ms": token_reserved_for_ms,
                    "spacing_ms": int(spacing_ms),
                },
                sort_keys=True,
            ),
            content_type="application/json",
        )
        acquire_finished_at = utc_now_precise()
        wait_ms = max(0, token_reserved_for_ms - int(time.time() * 1000))
        result = {
            "limiter_enabled": True,
            "limiter_key": limiter_key,
            "limiter_acquire_started_at": acquire_started_at,
            "limiter_acquire_finished_at": acquire_finished_at,
            "limiter_attempts": attempts,
            "limiter_spacing_ms": int(spacing_ms),
            "limiter_previous_next_allowed_ms": previous_start_ms,
            "limiter_token_reserved_for_ms": token_reserved_for_ms,
            "limiter_wait_ms": wait_ms,
        }
    finally:
        try:
            if lock_generation is not None:
                lock_blob.delete(if_generation_match=lock_generation)
        except (NotFound, PreconditionFailed):
            pass
    if result["limiter_wait_ms"]:
        time.sleep(result["limiter_wait_ms"] / 1000)
    return result


def get_manifest_job(client, job_id):
    query = f"""
    SELECT
      job_id,
      run_id,
      provider,
      postcode,
      planned_window,
      scheduled_at,
      COALESCE(max_attempts, 3) AS max_attempts
    FROM `{config.table_id("job_manifest")}`
    WHERE job_id = @job_id
    ORDER BY created_at DESC
    LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("job_id", "STRING", job_id)]
    )
    rows = list(client.query(query, job_config=job_config, location=config.LOCATION).result())
    return dict(rows[0]) if rows else None


def get_success_event(client, job_id):
    query = f"""
    SELECT raw_uri, processed_rows
    FROM `{config.table_id("job_events")}`
    WHERE job_id = @job_id
      AND event_type = 'succeeded'
    ORDER BY event_time DESC
    LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("job_id", "STRING", job_id)]
    )
    rows = list(client.query(query, job_config=job_config, location=config.LOCATION).result())
    return dict(rows[0]) if rows else None


def count_attempt_events(client, job_id):
    query = f"""
    SELECT COUNT(*) AS attempts
    FROM `{config.table_id("job_events")}`
    WHERE job_id = @job_id
      AND event_type = 'started'
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("job_id", "STRING", job_id)]
    )
    rows = list(client.query(query, job_config=job_config, location=config.LOCATION).result())
    return int(rows[0]["attempts"]) if rows else 0


def upload_raw(storage_client, payload, captured_at, snapshot_id, source_url, data):
    date_part = captured_at.date().isoformat()
    blob_name = (
        f"raw/provider={payload['provider']}/date={date_part}/"
        f"window={payload['planned_window']}/"
        f"postcode={payload['postcode']}_job={payload['job_id']}.json.gz"
    )
    raw_payload = {
        "snapshot_id": snapshot_id,
        "job_id": payload["job_id"],
        "provider": payload["provider"],
        "postcode": payload["postcode"],
        "planned_window": payload["planned_window"],
        "scheduled_at": payload["scheduled_at"],
        "captured_at": captured_at.isoformat(),
        "source_url": source_url,
        "response": data,
    }
    compressed = gzip.compress(json.dumps(raw_payload, ensure_ascii=False).encode("utf-8"))
    bucket = storage_client.bucket(config.BUCKET_NAME)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(compressed, content_type="application/gzip")
    return f"gs://{config.BUCKET_NAME}/{blob_name}"


def insert_snapshots(client, payload, captured_at, snapshot_id, rows):
    bq_rows = []
    for row in rows:
        bq_rows.append(
            {
                "snapshot_id": snapshot_id,
                "job_id": payload["job_id"],
                "provider": payload["provider"],
                "postcode": payload["postcode"],
                "planned_window": payload["planned_window"],
                "scheduled_at": payload["scheduled_at"],
                "captured_at": captured_at.isoformat(),
                **row,
            }
        )
    if not bq_rows:
        return 0
    errors = client.insert_rows_json(config.table_id(config.SNAPSHOTS_TABLE), bq_rows)
    if errors:
        raise RuntimeError(f"BigQuery restaurant snapshot insert errors: {errors[:3]}")
    return len(bq_rows)


@app.get("/")
def health():
    return jsonify({"ok": True, "service": "delivery-task-worker"})


@app.post("/tasks/justeat")
def handle_justeat_task():
    worker_received_at = utc_now_precise()
    payload = request.get_json(force=True)
    required = {"job_id", "run_id", "provider", "postcode", "planned_window", "scheduled_at"}
    missing = sorted(required - set(payload))
    if missing:
        return jsonify({"ok": False, "error": f"Missing fields: {', '.join(missing)}"}), 400

    bq_client = bigquery.Client(project=config.PROJECT_ID, location=config.LOCATION)
    storage_client = storage.Client(project=config.PROJECT_ID)
    payload["postcode"] = clean_postcode(payload["postcode"])
    diagnostic = {
        "worker_received_at": worker_received_at,
        "limiter_enabled": env_bool("ENABLE_GLOBAL_JUSTEAT_RATE_LIMIT", False),
        "limiter_key": os.getenv("JUSTEAT_RATE_LIMIT_KEY", "justeat-api-global"),
        "limiter_spacing_ms": int(os.getenv("JUSTEAT_RATE_LIMIT_SPACING_MS", "1300")),
    }
    ban_enabled = env_bool("ENABLE_JUSTEAT_429_BAN_CIRCUIT", False)
    ban_key = os.getenv("JUSTEAT_BAN_KEY", diagnostic["limiter_key"])
    ban_seconds = int(os.getenv("JUSTEAT_BAN_SECONDS", "3600"))

    manifest_job = get_manifest_job(bq_client, payload["job_id"])
    if not manifest_job:
        insert_event(bq_client, payload, "rejected", message="Job is not in job_manifest")
        return jsonify({"ok": False, "error": "Job is not in job_manifest"}), 404

    success_event = get_success_event(bq_client, payload["job_id"])
    if success_event:
        return jsonify(
            {
                "ok": True,
                "skipped": "already_succeeded",
                "rows": success_event["processed_rows"],
                "raw_uri": success_event["raw_uri"],
            }
        )

    attempts = count_attempt_events(bq_client, payload["job_id"])
    if attempts >= int(manifest_job["max_attempts"]):
        insert_event(bq_client, payload, "rejected", message="Max attempts reached")
        return jsonify({"ok": True, "skipped": "max_attempts_reached"})

    diagnostic["worker_started_at"] = utc_now_precise()
    try:
        if ban_enabled:
            ban_until_ms, remaining_ms = active_ban_result(storage_client, ban_key)
            if ban_until_ms:
                message = (
                    "Global Just Eat 429 ban circuit active until "
                    f"{millis_to_datetime(ban_until_ms).isoformat()} "
                    f"({int(remaining_ms / 1000)}s remaining)"
                )
                diagnostic.update(
                    {
                        "outcome": "deferred_ban_active",
                        "error": message,
                    }
                )
                insert_diagnostic(bq_client, payload, diagnostic)
                insert_event(bq_client, payload, "deferred", message=message)
                print(
                    f"deferred job={payload.get('job_id')} postcode={payload.get('postcode')}: "
                    f"{message}",
                    flush=True,
                )
                return jsonify({"ok": False, "deferred": "ban_active", "error": message}), 503

        insert_event(bq_client, payload, "started")
        captured_at = utc_now()
        snapshot_id = str(uuid.uuid4())
        if diagnostic["limiter_enabled"]:
            diagnostic.update(
                acquire_global_rate_token(
                    storage_client,
                    diagnostic["limiter_key"],
                    diagnostic["limiter_spacing_ms"],
                )
            )
        data, source_url, api_metadata = fetch_listing_api(
            payload["postcode"],
            include_metadata=True,
        )
        diagnostic.update(api_metadata)
        diagnostic["api_url"] = source_url
        parsed_rows = parse_open_restaurants(data)
        raw_uri = upload_raw(storage_client, payload, captured_at, snapshot_id, source_url, data)
        inserted_rows = insert_snapshots(
            bq_client,
            payload,
            captured_at,
            snapshot_id,
            parsed_rows,
        )
        insert_event(
            bq_client,
            payload,
            "succeeded",
            raw_uri=raw_uri,
            processed_rows=inserted_rows,
        )
        diagnostic.update(
            {
                "outcome": "succeeded",
                "processed_rows": inserted_rows,
                "raw_uri": raw_uri,
            }
        )
        insert_diagnostic(bq_client, payload, diagnostic)
        print(
            f"succeeded job={payload['job_id']} postcode={payload['postcode']} "
            f"window={payload['planned_window']} rows={inserted_rows} "
            f"api_latency_ms={diagnostic.get('api_latency_ms')} "
            f"token_wait_ms={diagnostic.get('limiter_wait_ms')}",
            flush=True,
        )
        return jsonify({"ok": True, "rows": inserted_rows, "raw_uri": raw_uri})
    except Exception as exc:
        if isinstance(exc, JustEatAPIError):
            diagnostic.update(
                {
                    "api_url": exc.url,
                    "http_status": exc.status_code,
                    "api_latency_ms": exc.latency_ms,
                    "api_request_started_at": exc.started_at,
                    "api_request_finished_at": exc.finished_at,
                }
            )
            if ban_enabled and exc.status_code == 429:
                ban_until_ms = set_ban_state(
                    storage_client,
                    ban_key,
                    ban_seconds,
                    str(exc)[:1000],
                )
                diagnostic["error"] = (
                    f"{str(exc)[:1500]} | set_ban_until="
                    f"{millis_to_datetime(ban_until_ms).isoformat()}"
                )
        diagnostic.update(
            {
                "outcome": "failed",
                "error": diagnostic.get("error") or str(exc)[:2000],
            }
        )
        insert_diagnostic(bq_client, payload, diagnostic)
        insert_event(bq_client, payload, "failed", message=str(exc)[:2000])
        print(
            f"failed job={payload.get('job_id')} postcode={payload.get('postcode')}: {exc} "
            f"api_latency_ms={diagnostic.get('api_latency_ms')} "
            f"token_wait_ms={diagnostic.get('limiter_wait_ms')}",
            flush=True,
        )
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
