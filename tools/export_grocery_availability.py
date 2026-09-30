"""Export postcode-level Just Eat grocery availability for stakeholder use.

The script aggregates existing BigQuery outputs only. It does not call Just Eat,
create Cloud Tasks, or alter production tables.
"""

import argparse
import json
import os
import re
from pathlib import Path

import pandas as pd
from google.cloud import bigquery


DEFAULT_PROJECT_ID = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
DEFAULT_DATASET_ID = "delivery_availability"
DEFAULT_LOCATION = "europe-west2"
DEFAULT_OUTPUT = Path("data/output/justeat_grocery_availability_postcode.csv")
DEFAULT_WINDOWS_CONFIG = Path("configs/temporal_snapshot_windows_202606.json")


def clean_postcode(value):
    return re.sub(r"\s+", "", str(value)).lower()


def read_geography_map(england_wales_path, scotland_path):
    england_wales = pd.read_excel(
        england_wales_path,
        usecols=["LSOA21CD", "POSTCODE"],
        dtype=str,
    ).rename(columns={"LSOA21CD": "geo_code", "POSTCODE": "postcode"})
    england_wales["geo_type"] = "lsoa"

    scotland = pd.read_excel(
        scotland_path,
        sheet_name="Sheet1",
        usecols=["DZCode", "Postcode"],
        dtype=str,
    ).rename(columns={"DZCode": "geo_code", "Postcode": "postcode"})
    scotland["geo_type"] = "dz"

    geography = pd.concat([england_wales, scotland], ignore_index=True)
    geography = geography.dropna(subset=["postcode", "geo_code"])
    geography["postcode"] = geography["postcode"].map(clean_postcode)
    geography = geography[["postcode", "geo_code", "geo_type"]].drop_duplicates()

    # Boundary postcodes can be selected as the representative postcode for
    # more than one LSOA or Data Zone. Keep each geography assignment so the
    # export covers every supplied small area.
    return geography.sort_values("postcode").reset_index(drop=True)


def read_temporal_windows(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = []
    for source_tag, intervals in payload.items():
        local_times = {
            (
                pd.Timestamp(start).strftime("%H:%M"),
                pd.Timestamp(end).strftime("%H:%M"),
            )
            for start, end in intervals
        }
        if len(local_times) != 1:
            raise ValueError(
                f"Temporal tag {source_tag!r} has inconsistent local times: {local_times}"
            )
        window_start_local, window_end_local = local_times.pop()
        rows.append(
            {
                "source_dataset": "temporal_snapshot",
                "source_tag": source_tag,
                "window_start_local": window_start_local,
                "window_end_local": window_end_local,
            }
        )
    return pd.DataFrame(rows).sort_values(["window_start_local", "source_tag"])


def grocery_aggregate_query(project_id, dataset_id):
    profile_table = f"{project_id}.{dataset_id}.restaurant_profile"
    map_table = f"{project_id}.{dataset_id}.postcode_restaurant_delivery_map"
    temporal_table = (
        f"{project_id}.{dataset_id}.restaurant_snapshots_temporal_snapshot_202606"
    )
    return f"""
    WITH profiles AS (
      SELECT
        CAST(restaurant_id AS STRING) AS restaurant_id,
        LOWER(COALESCE(restaurant_name, '')) AS restaurant_name_lower,
        LOWER(COALESCE(cuisine_names, '')) AS cuisine_names_lower
      FROM `{profile_table}`
    ),
    classified_profiles AS (
      SELECT
        restaurant_id,
        CASE
          WHEN REGEXP_CONTAINS(restaurant_name_lower, r'^\\s*co[- ]?op\\b') THEN 'coop'
          WHEN REGEXP_CONTAINS(restaurant_name_lower, r'^\\s*morrisons\\b') THEN 'morrisons'
          WHEN REGEXP_CONTAINS(restaurant_name_lower, r'^\\s*sainsbury[^a-z0-9]?s\\b') THEN 'sainsburys'
          WHEN REGEXP_CONTAINS(restaurant_name_lower, r'^\\s*asda\\b')
            AND NOT REGEXP_CONTAINS(restaurant_name_lower, r'^\\s*asda\\s+cafe\\b') THEN 'asda'
          WHEN REGEXP_CONTAINS(restaurant_name_lower, r'^\\s*iceland\\b') THEN 'iceland'
          WHEN REGEXP_CONTAINS(restaurant_name_lower, r'^\\s*waitrose\\b') THEN 'waitrose'
          ELSE NULL
        END AS big_brand,
        REGEXP_CONTAINS(
          restaurant_name_lower,
          r'^\\s*(co[- ]?op|morrisons|sainsbury[^a-z0-9]?s|asda|iceland|waitrose|londis|one stop|spar|premier|nisa|budgens|costcutter|best[- ]?one|gopuff|family shopper|lifestyle express|go local|shop ?n ?drive)\\b'
        )
          AND NOT REGEXP_CONTAINS(restaurant_name_lower, r'^\\s*asda\\s+cafe\\b')
          AS is_general_grocery,
        REGEXP_CONTAINS(
          cuisine_names_lower,
          r'(^|,\\s*)groceries?(\\s*,|$)'
        )
          AND NOT REGEXP_CONTAINS(
            cuisine_names_lower,
            r'(^|,\\s*)(convenience|supermarket)(\\s*,|$)'
          ) AS has_justeat_groceries_tag
      FROM profiles
    ),
    static_restaurants AS (
      SELECT DISTINCT
        'static_map' AS source_dataset,
        'full_coverage' AS source_tag,
        map.postcode,
        profile.restaurant_id,
        profile.big_brand,
        profile.is_general_grocery,
        profile.has_justeat_groceries_tag
      FROM `{map_table}` AS map
      INNER JOIN classified_profiles AS profile
        ON map.restaurant_id = profile.restaurant_id
      WHERE map.is_delivery IS TRUE
    ),
    temporal_restaurants AS (
      SELECT DISTINCT
        'temporal_snapshot' AS source_dataset,
        snapshot.planned_window AS source_tag,
        snapshot.postcode,
        profile.restaurant_id,
        profile.big_brand,
        profile.is_general_grocery,
        profile.has_justeat_groceries_tag
      FROM `{temporal_table}` AS snapshot
      INNER JOIN classified_profiles AS profile
        ON CAST(snapshot.JustEatId AS STRING) = profile.restaurant_id
      WHERE snapshot.planned_window IN UNNEST(@temporal_tags)
    ),
    restaurants AS (
      SELECT * FROM static_restaurants
      UNION ALL
      SELECT * FROM temporal_restaurants
    )
    SELECT
      source_dataset,
      source_tag,
      postcode,
      COUNT(DISTINCT IF(big_brand IS NOT NULL, restaurant_id, NULL)) AS big_brand_grocery_count,
      COUNT(DISTINCT IF(is_general_grocery, restaurant_id, NULL)) AS general_grocery_count,
      COUNT(DISTINCT IF(has_justeat_groceries_tag, restaurant_id, NULL))
        AS justeat_groceries_cuisine_tag_count,
      IF(COUNT(DISTINCT IF(big_brand = 'coop', restaurant_id, NULL)) > 0, 'yes', 'no') AS has_coop,
      IF(COUNT(DISTINCT IF(big_brand = 'morrisons', restaurant_id, NULL)) > 0, 'yes', 'no') AS has_morrisons,
      IF(COUNT(DISTINCT IF(big_brand = 'sainsburys', restaurant_id, NULL)) > 0, 'yes', 'no') AS has_sainsburys,
      IF(COUNT(DISTINCT IF(big_brand = 'asda', restaurant_id, NULL)) > 0, 'yes', 'no') AS has_asda,
      IF(COUNT(DISTINCT IF(big_brand = 'iceland', restaurant_id, NULL)) > 0, 'yes', 'no') AS has_iceland,
      IF(COUNT(DISTINCT IF(big_brand = 'waitrose', restaurant_id, NULL)) > 0, 'yes', 'no') AS has_waitrose
    FROM restaurants
    GROUP BY source_dataset, source_tag, postcode
    """


def run_query(client, query, temporal_tags, dry_run):
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("temporal_tags", "STRING", temporal_tags)
        ],
        dry_run=dry_run,
        use_query_cache=not dry_run,
    )
    job = client.query(query, job_config=job_config)
    if dry_run:
        print(f"BigQuery bytes processed: {job.total_bytes_processed:,}")
        return None
    return pd.DataFrame([dict(row.items()) for row in job.result()])


def build_output(geography, source_keys, aggregates):
    static_keys = source_keys[source_keys["source_dataset"] == "static_map"].copy()
    temporal_keys = source_keys[
        source_keys["source_dataset"] == "temporal_snapshot"
    ].copy()

    static_grid = geography.merge(static_keys, how="cross")
    temporal_grid = geography.merge(temporal_keys, how="cross")
    output = pd.concat([static_grid, temporal_grid], ignore_index=True)
    output = output.merge(
        aggregates,
        on=["source_dataset", "source_tag", "postcode"],
        how="left",
    )

    count_columns = [
        "big_brand_grocery_count",
        "general_grocery_count",
        "justeat_groceries_cuisine_tag_count",
    ]
    for column in count_columns:
        output[column] = output[column].fillna(0).astype("int64")
    for column in [
        "has_coop",
        "has_morrisons",
        "has_sainsburys",
        "has_asda",
        "has_iceland",
        "has_waitrose",
    ]:
        output[column] = output[column].fillna("no")

    columns = [
        "postcode",
        "geo_code",
        "geo_type",
        "source_dataset",
        "source_tag",
        "window_start_local",
        "window_end_local",
        *count_columns,
        "has_coop",
        "has_morrisons",
        "has_sainsburys",
        "has_asda",
        "has_iceland",
        "has_waitrose",
    ]
    return output[columns].sort_values(
        ["source_dataset", "source_tag", "postcode"],
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export postcode-level Just Eat grocery availability from BigQuery."
    )
    parser.add_argument("--england-wales", required=True)
    parser.add_argument("--scotland", required=True)
    parser.add_argument("--windows-config", default=str(DEFAULT_WINDOWS_CONFIG))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--dataset-id", default=DEFAULT_DATASET_ID)
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show BigQuery bytes processed without exporting a CSV.",
    )
    args = parser.parse_args()
    if not args.project_id:
        parser.error("Set GCP_PROJECT_ID or pass --project-id.")
    return args


def main():
    args = parse_args()
    geography = read_geography_map(args.england_wales, args.scotland)
    temporal_keys = read_temporal_windows(args.windows_config)
    client = bigquery.Client(project=args.project_id, location=args.location)

    static_labels_query = f"""
    SELECT DISTINCT snapshot_label
    FROM `{args.project_id}.{args.dataset_id}.postcode_restaurant_delivery_map`
    ORDER BY snapshot_label
    """
    static_labels = [row["snapshot_label"] for row in client.query(static_labels_query)]
    if len(static_labels) != 1:
        raise RuntimeError(
            "Expected exactly one static-map snapshot label, found: "
            f"{static_labels}"
        )
    static_keys = pd.DataFrame(
        {
            "source_dataset": "static_map",
            "source_tag": ["full_coverage"],
            "window_start_local": None,
            "window_end_local": None,
        }
    )
    source_keys = pd.concat([static_keys, temporal_keys], ignore_index=True)

    aggregates = run_query(
        client,
        grocery_aggregate_query(args.project_id, args.dataset_id),
        temporal_keys["source_tag"].unique().tolist(),
        args.dry_run,
    )
    if args.dry_run:
        print(f"Geography rows: {len(geography):,}")
        print(f"Expected output rows: {len(geography) * len(source_keys):,}")
        return

    output = build_output(geography, source_keys, aggregates)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(destination, index=False, encoding="utf-8-sig")
    print(f"Exported {len(output):,} rows to {destination}")
    print("Rows by source tag:")
    print(output.groupby(["source_dataset", "source_tag"], dropna=False).size())


if __name__ == "__main__":
    main()
