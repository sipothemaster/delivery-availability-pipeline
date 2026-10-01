import gzip
import json
import os
import time
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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

MENU_CDN_BASE = "https://menu-globalmenucdn.je-apis.com"
MENU_USER_AGENT = (
    "DFRE-DeliveryAvailabilityResearch/1.0 "
    "(+https://github.com/sipothemaster/delivery-availability-pipeline)"
)


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


class MenuManifestError(RuntimeError):
    def __init__(
        self,
        message,
        url,
        status_code=None,
        latency_ms=None,
        started_at=None,
        finished_at=None,
    ):
        super().__init__(message)
        self.url = url
        self.status_code = status_code
        self.latency_ms = latency_ms
        self.started_at = started_at
        self.finished_at = finished_at


def fetch_menu_manifest_url(url):
    started_at = utc_now_precise()
    start_time = time.perf_counter()
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": MENU_USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            finished_at = utc_now_precise()
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            return json.loads(body), {
                "http_status": int(response.status),
                "latency_ms": latency_ms,
                "started_at": started_at,
                "finished_at": finished_at,
            }
    except HTTPError as exc:
        finished_at = utc_now_precise()
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        raise MenuManifestError(
            f"Menu manifest returned HTTP {exc.code}: {url}",
            url,
            status_code=int(exc.code),
            latency_ms=latency_ms,
            started_at=started_at,
            finished_at=finished_at,
        ) from exc
    except (URLError, TimeoutError) as exc:
        finished_at = utc_now_precise()
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        raise MenuManifestError(
            f"Menu manifest request failed: {url}: {exc}",
            url,
            latency_ms=latency_ms,
            started_at=started_at,
            finished_at=finished_at,
        ) from exc


def fetch_menu_manifest(slug):
    candidates = [
        ("original", f"{MENU_CDN_BASE}/{slug}_uk_manifest.json"),
        ("v2_2", f"{MENU_CDN_BASE}/v2_2/{slug}_uk_manifest.json"),
    ]
    errors = []
    for source, url in candidates:
        try:
            payload, metadata = fetch_menu_manifest_url(url)
            metadata.update(
                {
                    "manifest_source": source,
                    "manifest_url": url,
                    "fallback_used": source != "original",
                    "attempt_errors": "; ".join(errors),
                }
            )
            return payload, metadata
        except MenuManifestError as exc:
            errors.append(f"{source}:{exc.status_code or 'error'}")
            if source == "v2_2":
                exc.args = (f"{exc} | attempts={'; '.join(errors)}",)
                raise
    raise RuntimeError("unreachable")


def menu_manifest_result_row(payload, manifest_payload, metadata, fetched_at, outcome, error=None):
    info = manifest_payload.get("RestaurantInfo") if isinstance(manifest_payload, dict) else {}
    info = info or {}
    opening_times = info.get("RestaurantOpeningTimes") or []
    service_types = sorted(
        {
            item.get("ServiceType")
            for item in opening_times
            if isinstance(item, dict) and item.get("ServiceType")
        }
    )
    return {
        "result_id": str(uuid.uuid4()),
        "task_id": payload["task_id"],
        "run_id": payload["run_id"],
        "restaurant_id": str(payload["restaurant_id"]),
        "restaurant_name": payload.get("restaurant_name"),
        "restaurant_unique_name": payload["restaurant_unique_name"],
        "restaurant_url": payload.get("restaurant_url"),
        "cuisine_names": payload.get("cuisine_names"),
        "fetched_at": fetched_at.isoformat(),
        "manifest_source": metadata.get("manifest_source"),
        "manifest_url": metadata.get("manifest_url"),
        "http_status": metadata.get("http_status"),
        "latency_ms": metadata.get("latency_ms"),
        "fallback_used": metadata.get("fallback_used"),
        "manifest_restaurant_id": (
            str(manifest_payload.get("RestaurantId"))
            if isinstance(manifest_payload, dict) and manifest_payload.get("RestaurantId") is not None
            else None
        ),
        "manifest_name": info.get("Name"),
        "timezone": info.get("TimeZone"),
        "is_offline": info.get("IsOffline"),
        "menu_count": len(manifest_payload.get("Menus") or [])
        if isinstance(manifest_payload, dict)
        else None,
        "items_url": manifest_payload.get("ItemsUrl") if isinstance(manifest_payload, dict) else None,
        "item_details_url": (
            manifest_payload.get("ItemDetailsUrl") if isinstance(manifest_payload, dict) else None
        ),
        "opening_time_count": sum(
            len(day.get("Times") or [])
            for service in opening_times
            for day in (service.get("TimesPerDay") or [])
            if isinstance(day, dict)
        ),
        "opening_service_types": "|".join(service_types) or None,
        "outcome": outcome,
        "error": error,
    }


def time_crosses_midnight(opens_at, closes_at):
    if not opens_at or not closes_at:
        return None
    return str(closes_at) <= str(opens_at)


def opening_time_rows(payload, manifest_payload, fetched_at):
    info = manifest_payload.get("RestaurantInfo") or {}
    timezone_name = info.get("TimeZone")
    rows = []
    for service in info.get("RestaurantOpeningTimes") or []:
        service_type = service.get("ServiceType")
        for day in service.get("TimesPerDay") or []:
            day_of_week = day.get("DayOfWeek")
            for index, interval in enumerate(day.get("Times") or [], start=1):
                opens_at = interval.get("FromLocalTime")
                closes_at = interval.get("ToLocalTime")
                rows.append(
                    {
                        "opening_time_id": str(uuid.uuid4()),
                        "task_id": payload["task_id"],
                        "run_id": payload["run_id"],
                        "restaurant_id": str(payload["restaurant_id"]),
                        "restaurant_unique_name": payload["restaurant_unique_name"],
                        "service_type": service_type,
                        "day_of_week": day_of_week,
                        "interval_index": index,
                        "opens_at_local": opens_at,
                        "closes_at_local": closes_at,
                        "crosses_midnight": time_crosses_midnight(opens_at, closes_at),
                        "timezone": timezone_name,
                        "source": "menu_manifest",
                        "fetched_at": fetched_at.isoformat(),
                    }
                )
    return rows


def insert_menu_manifest_result(client, row):
    errors = client.insert_rows_json(config.table_id("menu_manifest_results"), [row])
    if errors:
        raise RuntimeError(f"BigQuery menu_manifest_results insert errors: {errors[:3]}")


def insert_opening_times(client, rows):
    if not rows:
        return 0
    errors = client.insert_rows_json(config.table_id("restaurant_opening_times"), rows)
    if errors:
        raise RuntimeError(f"BigQuery restaurant_opening_times insert errors: {errors[:3]}")
    return len(rows)


def menu_manifest_success_exists(client, task_id):
    query = f"""
    SELECT 1
    FROM `{config.table_id("menu_manifest_results")}`
    WHERE task_id = @task_id
      AND outcome = 'succeeded'
    LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("task_id", "STRING", task_id)]
    )
    rows = list(client.query(query, job_config=job_config, location=config.LOCATION).result())
    return bool(rows)


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


@app.post("/tasks/justeat-menu-manifest")
def handle_justeat_menu_manifest_task():
    worker_received_at = utc_now_precise()
    payload = request.get_json(force=True)
    required = {
        "task_id",
        "run_id",
        "restaurant_id",
        "restaurant_unique_name",
        "scheduled_at",
    }
    missing = sorted(required - set(payload))
    if missing:
        return jsonify({"ok": False, "error": f"Missing fields: {', '.join(missing)}"}), 400

    bq_client = bigquery.Client(project=config.PROJECT_ID, location=config.LOCATION)
    storage_client = storage.Client(project=config.PROJECT_ID)
    payload["restaurant_unique_name"] = str(payload["restaurant_unique_name"]).strip()

    if menu_manifest_success_exists(bq_client, payload["task_id"]):
        return jsonify({"ok": True, "skipped": "already_succeeded"})

    diagnostic = {
        "worker_received_at": worker_received_at,
        "limiter_enabled": env_bool("ENABLE_GLOBAL_MENU_MANIFEST_RATE_LIMIT", True),
        "limiter_key": os.getenv(
            "MENU_MANIFEST_RATE_LIMIT_KEY", "justeat-menu-manifest-1000ms"
        ),
        "limiter_spacing_ms": int(os.getenv("MENU_MANIFEST_RATE_LIMIT_SPACING_MS", "1000")),
    }

    fetched_at = utc_now_precise()
    manifest_payload = {}
    metadata = {}
    try:
        if diagnostic["limiter_enabled"]:
            diagnostic.update(
                acquire_global_rate_token(
                    storage_client,
                    diagnostic["limiter_key"],
                    diagnostic["limiter_spacing_ms"],
                )
            )

        manifest_payload, metadata = fetch_menu_manifest(payload["restaurant_unique_name"])
        fetched_at = metadata.get("finished_at") or utc_now_precise()
        result = menu_manifest_result_row(
            payload,
            manifest_payload,
            metadata,
            fetched_at,
            outcome="succeeded",
        )
        opening_rows = opening_time_rows(payload, manifest_payload, fetched_at)
        insert_menu_manifest_result(bq_client, result)
        inserted_opening_rows = insert_opening_times(bq_client, opening_rows)
        print(
            f"menu_manifest succeeded task={payload['task_id']} "
            f"restaurant_id={payload['restaurant_id']} "
            f"slug={payload['restaurant_unique_name']} "
            f"source={metadata.get('manifest_source')} "
            f"latency_ms={metadata.get('latency_ms')} "
            f"token_wait_ms={diagnostic.get('limiter_wait_ms')} "
            f"opening_rows={inserted_opening_rows}",
            flush=True,
        )
        return jsonify(
            {
                "ok": True,
                "manifest_source": metadata.get("manifest_source"),
                "latency_ms": metadata.get("latency_ms"),
                "opening_rows": inserted_opening_rows,
            }
        )
    except Exception as exc:
        if isinstance(exc, MenuManifestError):
            metadata = {
                "manifest_url": exc.url,
                "http_status": exc.status_code,
                "latency_ms": exc.latency_ms,
                "manifest_source": None,
                "fallback_used": True,
            }
            fetched_at = exc.finished_at or utc_now_precise()
        result = menu_manifest_result_row(
            payload,
            manifest_payload,
            metadata,
            fetched_at,
            outcome="failed",
            error=str(exc)[:2000],
        )
        try:
            insert_menu_manifest_result(bq_client, result)
        except Exception as insert_exc:
            print(f"menu_manifest failed-result insert error: {insert_exc}", flush=True)
        print(
            f"menu_manifest failed task={payload.get('task_id')} "
            f"restaurant_id={payload.get('restaurant_id')} "
            f"slug={payload.get('restaurant_unique_name')}: {exc} "
            f"token_wait_ms={diagnostic.get('limiter_wait_ms')}",
            flush=True,
        )
        return jsonify({"ok": False, "error": str(exc)}), 500


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
        "limiter_enabled": env_bool("ENABLE_GLOBAL_JUSTEAT_RATE_LIMIT", True),
        "limiter_key": os.getenv("JUSTEAT_RATE_LIMIT_KEY", "justeat-api-global"),
        "limiter_spacing_ms": int(os.getenv("JUSTEAT_RATE_LIMIT_SPACING_MS", "1300")),
    }
    ban_enabled = env_bool("ENABLE_JUSTEAT_429_BAN_CIRCUIT", True)
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
