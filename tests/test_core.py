import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cloud_pipeline import run_task_creator_job
from cloud_pipeline.create_tasks_cloud import (
    CLOUD_TASK_DELIVERY_MAX_ATTEMPTS,
    build_payloads,
    stable_job_id,
)
from cloud_pipeline.create_week_jobs_cloud import (
    schedule_across_intervals,
    schedule_across_intervals_with_bounds,
)
from cloud_pipeline.justeat_api import api_restaurant_to_row
from cloud_pipeline.task_worker_service import (
    manifest_payload_mismatches,
    opening_time_rows,
    parse_open_restaurants,
    task_window_expired,
)


FIXTURES = Path(__file__).parent / "fixtures"
LONDON = ZoneInfo("Europe/London")


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ListingParserTests(unittest.TestCase):
    def test_open_delivery_filter_and_grocery_label(self):
        rows = parse_open_restaurants(load_fixture("listing_response.json"))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["JustEatId"], "1001")
        self.assertEqual(rows[0]["Vertical"], "groceries")
        self.assertEqual(rows[0]["DeliveryFee"], "\u00a31.50")
        self.assertEqual(
            rows[0]["Url"],
            "https://www.just-eat.co.uk/restaurants-example-grocery/menu",
        )

    def test_profile_row_without_grocery_filter_defaults_to_restaurants(self):
        restaurant = load_fixture("listing_response.json")["restaurants"][0]
        row = api_restaurant_to_row(restaurant)

        self.assertEqual(row["Vertical"], "restaurants")
        self.assertEqual(row["DeliveryTime"], "20-35 mins")


class SchedulingTests(unittest.TestCase):
    def test_schedule_is_inside_intervals_and_keeps_end_guard(self):
        intervals = [
            ("2026-06-17T14:00:00", "2026-06-17T18:00:00"),
            ("2026-06-18T14:00:00", "2026-06-18T18:00:00"),
        ]
        scheduled = schedule_across_intervals(intervals, 100)
        guarded = [
            (
                datetime.fromisoformat(start).replace(tzinfo=LONDON),
                datetime.fromisoformat(end).replace(tzinfo=LONDON),
            )
            for start, end in intervals
        ]

        self.assertEqual(len(scheduled), 100)
        self.assertEqual(scheduled, sorted(scheduled))
        for value in scheduled:
            local_value = value.astimezone(LONDON)
            self.assertTrue(
                any(start <= local_value < end for start, end in guarded)
            )
            self.assertTrue(all(local_value != end for _, end in guarded))
        self.assertLessEqual(
            scheduled[-1].astimezone(LONDON),
            guarded[-1][1] - timedelta(seconds=60),
        )

    def test_job_identifier_is_deterministic_and_window_specific(self):
        first = stable_job_id("run-1", "just_eat", "weekday_evening", "ls42sw")
        second = stable_job_id("run-1", "just_eat", "weekday_evening", "ls42sw")
        other_window = stable_job_id(
            "run-1", "just_eat", "saturday_peak", "ls42sw"
        )

        self.assertEqual(first, second)
        self.assertNotEqual(first, other_window)

    def test_payload_carries_its_interval_end_without_changing_manifest_schema(self):
        windows = {
            "weekday_afternoon": [
                ("2026-06-17T14:00:00", "2026-06-17T18:00:00")
            ]
        }
        payloads, manifest_rows = build_payloads(
            ["aa11aa"],
            ["weekday_afternoon"],
            windows,
            "just_eat",
            "run-1",
            3,
            "queue-1",
        )

        self.assertEqual(payloads[0]["window_end_at"], "2026-06-17T17:00:00+00:00")
        self.assertNotIn("window_end_at", manifest_rows[0])

    def test_window_expiry_uses_timezone_aware_end(self):
        payload = {"window_end_at": "2026-06-17T17:00:00+00:00"}

        self.assertFalse(
            task_window_expired(
                payload,
                now=datetime(2026, 6, 17, 16, 59, tzinfo=ZoneInfo("UTC")),
            )
        )
        self.assertTrue(
            task_window_expired(
                payload,
                now=datetime(2026, 6, 17, 17, 0, tzinfo=ZoneInfo("UTC")),
            )
        )

    def test_queue_retry_budget_can_span_the_one_hour_circuit(self):
        self.assertGreaterEqual(CLOUD_TASK_DELIVERY_MAX_ATTEMPTS, 12)

    def test_worker_rejects_manifest_identity_mismatch(self):
        manifest = {
            "run_id": "run-1",
            "provider": "just_eat",
            "postcode": "ls42sw",
            "planned_window": "weekday_evening",
        }
        payload = {**manifest, "postcode": "LS4 2SW"}

        self.assertEqual(manifest_payload_mismatches(manifest, payload), [])
        payload["planned_window"] = "saturday_peak"
        self.assertEqual(
            manifest_payload_mismatches(manifest, payload),
            ["planned_window"],
        )


class OpeningTimeTests(unittest.TestCase):
    def test_opening_intervals_are_normalised_and_cross_midnight_is_preserved(self):
        payload = {
            "task_id": "task-1",
            "run_id": "run-1",
            "restaurant_id": "1001",
            "restaurant_unique_name": "example-grocery",
        }
        fetched_at = datetime(2026, 6, 1, 12, tzinfo=ZoneInfo("UTC"))

        rows = opening_time_rows(
            payload,
            load_fixture("menu_manifest.json"),
            fetched_at,
        )

        self.assertEqual(len(rows), 3)
        self.assertEqual([row["interval_index"] for row in rows], [1, 2, 1])
        self.assertFalse(rows[0]["crosses_midnight"])
        self.assertTrue(rows[2]["crosses_midnight"])
        self.assertEqual(rows[2]["day_of_week"], "Friday")


class TaskCreatorEntrypointTests(unittest.TestCase):
    def test_safe_queue_defaults_are_forwarded(self):
        environment = {
            "POSTCODE_FILE": "postcodes.csv",
            "TASK_WORKER_SERVICE_URL": "https://worker.example.invalid",
            "OIDC_SERVICE_ACCOUNT_EMAIL": "worker@example.invalid",
        }
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(run_task_creator_job.create_tasks_cloud, "main"):
                with patch.object(sys, "argv", ["test"]):
                    run_task_creator_job.main()
                    forwarded = list(sys.argv)

        rate_index = forwarded.index("--max-dispatches-per-second")
        concurrency_index = forwarded.index("--max-concurrent-dispatches")
        self.assertEqual(forwarded[rate_index + 1], "1")
        self.assertEqual(forwarded[concurrency_index + 1], "4")


if __name__ == "__main__":
    unittest.main()
