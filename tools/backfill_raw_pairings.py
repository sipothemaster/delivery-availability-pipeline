import argparse
import gzip
import json
import re
from datetime import datetime, timezone

from google.cloud import bigquery, storage


DEFAULT_PROJECT_ID = "delivery-availability-research"
DEFAULT_DATASET_ID = "delivery_availability"
DEFAULT_LOCATION = "europe-west2"
DEFAULT_MAP_TABLE = "postcode_restaurant_delivery_map_test"
DEFAULT_PROFILE_TABLE = "restaurant_profile_test"
DEFAULT_SNAPSHOT_LABEL = "weekday_full_20260520"
JUSTEAT_BASE_URL = "https://www.just-eat.co.uk"


DELIVERY_MAP_SCHEMA = [
    bigquery.SchemaField("snapshot_label", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("postcode", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("restaurant_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("is_delivery", "BOOLEAN"),
    bigquery.SchemaField("delivery_fee", "FLOAT"),
    bigquery.SchemaField("minimum_delivery_value", "FLOAT"),
    bigquery.SchemaField("delivery_eta_lower_minutes", "INTEGER"),
    bigquery.SchemaField("delivery_eta_upper_minutes", "INTEGER"),
    bigquery.SchemaField("drive_distance_meters", "INTEGER"),
]


RESTAURANT_PROFILE_SCHEMA = [
    bigquery.SchemaField("restaurant_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("restaurant_name", "STRING"),
    bigquery.SchemaField("restaurant_unique_name", "STRING"),
    bigquery.SchemaField("restaurant_url", "STRING"),
    bigquery.SchemaField("address_first_line", "STRING"),
    bigquery.SchemaField("city", "STRING"),
    bigquery.SchemaField("postal_code", "STRING"),
    bigquery.SchemaField("latitude", "FLOAT"),
    bigquery.SchemaField("longitude", "FLOAT"),
    bigquery.SchemaField("cuisine_names", "STRING"),
    bigquery.SchemaField("cuisine_unique_names", "STRING"),
    bigquery.SchemaField("rating_count", "INTEGER"),
    bigquery.SchemaField("rating_star", "FLOAT"),
    bigquery.SchemaField("logo_url", "STRING"),
]


def parse_gcs_uri(uri):
    if not uri.startswith("gs://"):
        raise ValueError(f"raw URI must start with gs://, got: {uri}")
    match = re.match(r"^gs://([^/]+)/(.+)$", uri)
    if not match:
        raise ValueError(f"raw URI must include bucket and object path: {uri}")
    return match.group(1), match.group(2)


def read_gcs_gzip_json(storage_client, raw_uri):
    bucket_name, blob_name = parse_gcs_uri(raw_uri)
    blob = storage_client.bucket(bucket_name).blob(blob_name)
    raw_bytes = blob.download_as_bytes()
    json_text = gzip.decompress(raw_bytes).decode("utf-8")
    return json.loads(json_text)


def get_nested(data, path):
    current = data
    for key in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def get_restaurant_id(restaurant):
    value = (
        restaurant.get("id")
        or restaurant.get("restaurantId")
        or restaurant.get("uniqueName")
        or restaurant.get("seoName")
    )
    return str(value) if value is not None else None


def restaurant_url(unique_name):
    if not unique_name:
        return None
    return f"{JUSTEAT_BASE_URL}/restaurants-{unique_name}/menu"


def split_coordinates(restaurant):
    coordinates = get_nested(restaurant, "address.location.coordinates")
    if isinstance(coordinates, list) and len(coordinates) >= 2:
        longitude, latitude = coordinates[0], coordinates[1]
        return latitude, longitude
    return None, None


def join_values(items, key):
    values = [item.get(key) for item in items or [] if item.get(key)]
    return ", ".join(values) if values else None


def restaurant_to_profile_row(restaurant):
    restaurant_id = get_restaurant_id(restaurant)
    if not restaurant_id:
        return None
    latitude, longitude = split_coordinates(restaurant)
    cuisines = restaurant.get("cuisines") or []
    unique_name = restaurant.get("uniqueName")
    return {
        "restaurant_id": restaurant_id,
        "restaurant_name": restaurant.get("name"),
        "restaurant_unique_name": unique_name,
        "restaurant_url": restaurant_url(unique_name),
        "address_first_line": get_nested(restaurant, "address.firstLine"),
        "city": get_nested(restaurant, "address.city"),
        "postal_code": get_nested(restaurant, "address.postalCode"),
        "latitude": latitude,
        "longitude": longitude,
        "cuisine_names": join_values(cuisines, "name"),
        "cuisine_unique_names": join_values(cuisines, "uniqueName"),
        "rating_count": get_nested(restaurant, "rating.count"),
        "rating_star": get_nested(restaurant, "rating.starRating"),
        "logo_url": restaurant.get("logoUrl"),
    }


def restaurant_to_map_row(payload, restaurant, snapshot_label):
    restaurant_id = get_restaurant_id(restaurant)
    if not restaurant_id:
        return None
    eta = restaurant.get("deliveryEtaMinutes") or {}
    return {
        "snapshot_label": snapshot_label,
        "postcode": payload.get("postcode"),
        "restaurant_id": restaurant_id,
        "is_delivery": restaurant.get("isDelivery"),
        "delivery_fee": restaurant.get("deliveryCost"),
        "minimum_delivery_value": restaurant.get("minimumDeliveryValue"),
        "delivery_eta_lower_minutes": eta.get("rangeLower"),
        "delivery_eta_upper_minutes": eta.get("rangeUpper"),
        "drive_distance_meters": restaurant.get("driveDistanceMeters"),
    }


def extract_rows(payload, snapshot_label):
    restaurants = (payload.get("response") or {}).get("restaurants") or []
    map_rows = []
    profile_rows_by_id = {}
    skipped_without_id = 0

    for restaurant in restaurants:
        map_row = restaurant_to_map_row(payload, restaurant, snapshot_label)
        profile_row = restaurant_to_profile_row(restaurant)
        if not map_row or not profile_row:
            skipped_without_id += 1
            continue
        map_rows.append(map_row)
        profile_rows_by_id[profile_row["restaurant_id"]] = profile_row

    return map_rows, list(profile_rows_by_id.values()), skipped_without_id


def build_table_id(project_id, dataset_id, table_name):
    if table_name.count(".") == 2:
        return table_name
    return f"{project_id}.{dataset_id}.{table_name}"


def ensure_table(bigquery_client, table_id, schema):
    try:
        return bigquery_client.get_table(table_id)
    except Exception as exc:
        if exc.__class__.__name__ != "NotFound":
            raise
    table = bigquery.Table(table_id, schema=schema)
    return bigquery_client.create_table(table)


def insert_rows(bigquery_client, table_id, rows):
    if not rows:
        return 0
    errors = bigquery_client.insert_rows_json(table_id, rows)
    if errors:
        raise RuntimeError(f"BigQuery insert failed for {table_id}: {errors[:3]}")
    return len(rows)


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def existing_restaurant_ids(bigquery_client, profile_table_id, restaurant_ids):
    existing = set()
    unique_ids = sorted(set(restaurant_ids))
    if not unique_ids:
        return existing

    query = f"""
    SELECT restaurant_id
    FROM `{profile_table_id}`
    WHERE restaurant_id IN UNNEST(@restaurant_ids)
    """
    for batch in chunks(unique_ids, 10000):
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("restaurant_ids", "STRING", batch)
            ]
        )
        for row in bigquery_client.query(query, job_config=job_config).result():
            existing.add(row["restaurant_id"])
    return existing


def query_raw_uris(bigquery_client, project_id, dataset_id, source_events_table, limit=None):
    source_table_id = build_table_id(project_id, dataset_id, source_events_table)
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    query = f"""
    SELECT raw_uri
    FROM `{source_table_id}`
    WHERE event_type = 'succeeded'
      AND raw_uri IS NOT NULL
    ORDER BY event_time
    {limit_sql}
    """
    return [row["raw_uri"] for row in bigquery_client.query(query).result()]


def raw_uris_from_args(bigquery_client, args):
    if args.raw_uri:
        return [args.raw_uri]
    return query_raw_uris(
        bigquery_client,
        args.project_id,
        args.dataset_id,
        args.source_events_table,
        limit=args.limit_raw_files,
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Backfill static Just Eat postcode-restaurant map and profile tables."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--raw-uri", help="GCS URI for one .json.gz raw file.")
    source.add_argument(
        "--source-events-table",
        help="BigQuery job_events table to read succeeded raw_uri values from.",
    )
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--dataset-id", default=DEFAULT_DATASET_ID)
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--snapshot-label", default=DEFAULT_SNAPSHOT_LABEL)
    parser.add_argument("--map-table", default=DEFAULT_MAP_TABLE)
    parser.add_argument("--profile-table", default=DEFAULT_PROFILE_TABLE)
    parser.add_argument("--limit-raw-files", type=int)
    parser.add_argument("--insert-batch-size", type=int, default=5000)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print counts without creating tables or writing rows.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    map_table_id = build_table_id(args.project_id, args.dataset_id, args.map_table)
    profile_table_id = build_table_id(args.project_id, args.dataset_id, args.profile_table)
    storage_client = storage.Client(project=args.project_id)
    bigquery_client = bigquery.Client(project=args.project_id, location=args.location)

    raw_uris = raw_uris_from_args(bigquery_client, args)
    print(f"raw files to process: {len(raw_uris)}")
    print(f"map table: {map_table_id}")
    print(f"profile table: {profile_table_id}")
    print(f"snapshot_label: {args.snapshot_label}")

    all_profile_rows_by_id = {}
    map_buffer = []
    total_map_rows = 0
    total_profiles_seen = 0
    total_skipped_without_id = 0

    if not args.dry_run:
        ensure_table(bigquery_client, map_table_id, DELIVERY_MAP_SCHEMA)
        ensure_table(bigquery_client, profile_table_id, RESTAURANT_PROFILE_SCHEMA)

    for index, raw_uri in enumerate(raw_uris, start=1):
        payload = read_gcs_gzip_json(storage_client, raw_uri)
        map_rows, profile_rows, skipped_without_id = extract_rows(
            payload,
            args.snapshot_label,
        )
        total_map_rows += len(map_rows)
        total_profiles_seen += len(profile_rows)
        total_skipped_without_id += skipped_without_id
        for profile_row in profile_rows:
            all_profile_rows_by_id[profile_row["restaurant_id"]] = profile_row

        if args.dry_run:
            if index == 1:
                print("sample map rows:")
                for row in map_rows[:5]:
                    print(json.dumps(row, ensure_ascii=False))
                print("sample profile rows:")
                for row in profile_rows[:5]:
                    print(json.dumps(row, ensure_ascii=False))
            continue

        map_buffer.extend(map_rows)
        if len(map_buffer) >= args.insert_batch_size:
            inserted = insert_rows(bigquery_client, map_table_id, map_buffer)
            print(f"inserted map rows: {inserted} | raw files processed: {index}")
            map_buffer = []

    if args.dry_run:
        print(f"map rows parsed: {total_map_rows}")
        print(f"unique profile rows parsed: {len(all_profile_rows_by_id)}")
        print(f"profile rows seen before de-dupe: {total_profiles_seen}")
        print(f"restaurants skipped without id: {total_skipped_without_id}")
        print("Dry run only; no BigQuery tables were created and no rows were inserted.")
        return

    if map_buffer:
        inserted = insert_rows(bigquery_client, map_table_id, map_buffer)
        print(f"inserted final map rows: {inserted}")

    all_profile_rows = list(all_profile_rows_by_id.values())
    existing_ids = existing_restaurant_ids(
        bigquery_client,
        profile_table_id,
        [row["restaurant_id"] for row in all_profile_rows],
    )
    new_profile_rows = [
        row for row in all_profile_rows if row["restaurant_id"] not in existing_ids
    ]
    profile_inserted = 0
    for batch in chunks(new_profile_rows, args.insert_batch_size):
        profile_inserted += insert_rows(bigquery_client, profile_table_id, batch)

    print(f"map rows parsed: {total_map_rows}")
    print(f"unique profile rows parsed: {len(all_profile_rows)}")
    print(f"profile rows inserted: {profile_inserted}")
    print(f"profile rows already existed: {len(existing_ids)}")
    print(f"restaurants skipped without id: {total_skipped_without_id}")


if __name__ == "__main__":
    main()
