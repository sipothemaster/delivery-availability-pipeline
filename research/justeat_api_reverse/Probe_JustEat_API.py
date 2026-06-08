import argparse
import csv
import json
import os
import re
import time
from urllib.parse import parse_qsl, urlparse

from playwright.sync_api import sync_playwright

from Scrape_JustEat import USER_AGENT, build_area_url, clean_postcode, parse_verticals

DEFAULT_OUTPUT = "data/output/JustEat_network_probe.csv"


def clean_text(value):
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value)).strip()


def looks_interesting(url, content_type, body_sample):
    haystack = f"{url} {content_type} {body_sample}".lower()
    keywords = (
        "restaurant",
        "restaurants",
        "grocery",
        "groceries",
        "delivery",
        "eta",
        "rating",
        "postcode",
        "vertical",
        "search",
        "discovery",
        "places",
    )
    return any(keyword in haystack for keyword in keywords)


def summarise_json(value):
    summary = {
        "top_level_type": type(value).__name__,
        "top_level_keys": None,
        "list_lengths": [],
        "restaurant_like_count": 0,
    }

    if isinstance(value, dict):
        summary["top_level_keys"] = ",".join(list(value.keys())[:30])

    def walk(node, path="$"):
        if isinstance(node, list):
            summary["list_lengths"].append(f"{path}:{len(node)}")
            for item in node[:50]:
                walk(item, path + "[]")
        elif isinstance(node, dict):
            keys = {str(key).lower() for key in node.keys()}
            if (
                {"name", "rating"} & keys
                or {"restaurantid", "restaurantname"} & keys
                or "cuisines" in keys
                or "deliveryeta" in keys
            ):
                summary["restaurant_like_count"] += 1
            for key, item in list(node.items())[:80]:
                walk(item, f"{path}.{key}")

    walk(value)
    summary["list_lengths"] = "; ".join(summary["list_lengths"][:20])
    return summary


def response_row(response, request, started_at):
    url = response.url
    content_type = response.headers.get("content-type", "")
    row = {
        "elapsed_seconds": f"{time.perf_counter() - started_at:.3f}",
        "method": request.method,
        "status": response.status,
        "resource_type": request.resource_type,
        "content_type": content_type,
        "url": url,
        "url_host": urlparse(url).netloc,
        "url_path": urlparse(url).path,
        "query_keys": ",".join(key for key, _ in parse_qsl(urlparse(url).query)),
        "post_data": clean_text(request.post_data)[:500] if request.post_data else None,
        "body_sample": None,
        "body_bytes": None,
        "json_top_level_type": None,
        "json_top_level_keys": None,
        "json_list_lengths": None,
        "restaurant_like_count": 0,
        "error": None,
    }

    try:
        body = response.body()
        row["body_bytes"] = len(body)
        text = body.decode("utf-8", errors="replace")
        row["body_sample"] = clean_text(text[:1000])
        if "json" in content_type.lower() or text.lstrip().startswith(("{", "[")):
            parsed = json.loads(text)
            summary = summarise_json(parsed)
            row["json_top_level_type"] = summary["top_level_type"]
            row["json_top_level_keys"] = summary["top_level_keys"]
            row["json_list_lengths"] = summary["list_lengths"]
            row["restaurant_like_count"] = summary["restaurant_like_count"]
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"

    return row


def save_rows(rows, output_path):
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    if not rows:
        return
    with open(output_path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def parse_args():
    parser = argparse.ArgumentParser(description="Probe Just Eat network calls.")
    parser.add_argument("--postcode", default="ls42sw")
    parser.add_argument("--verticals", nargs="+", default=["restaurants"])
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--scroll-delay", type=float, default=0.7)
    parser.add_argument("--scroll-pixels", type=int, default=8000)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--all-resources",
        action="store_true",
        help="Record interesting responses from every resource type, not only fetch/xhr.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    postcode = clean_postcode(args.postcode)
    verticals = parse_verticals(args.verticals)
    rows = []
    seen = set()
    started_at = time.perf_counter()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=args.headless)
        page = browser.new_page(
            viewport={"width": 1365, "height": 900},
            user_agent=USER_AGENT,
        )

        def on_response(response):
            request = response.request
            if not args.all_resources and request.resource_type not in {"fetch", "xhr"}:
                return
            key = (request.method, response.url, request.post_data or "")
            if key in seen:
                return
            seen.add(key)
            row = response_row(response, request, started_at)
            if looks_interesting(row["url"], row["content_type"], row["body_sample"] or ""):
                rows.append(row)
                print(
                    f"{row['status']} {row['method']} {row['url_path']} "
                    f"bytes={row['body_bytes']} restaurant_like={row['restaurant_like_count']}"
                )

        page.on("response", on_response)

        for vertical in verticals:
            url = build_area_url(postcode, vertical, open_now=True)
            print(f"Opening {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector('[data-qa="restaurant-card"]', timeout=15000)
            page.wait_for_timeout(1000)
            for _ in range(args.rounds):
                page.mouse.wheel(0, args.scroll_pixels)
                page.wait_for_timeout(int(args.scroll_delay * 1000))

        browser.close()

    save_rows(rows, args.output)
    print(f"Saved {len(rows)} interesting network rows -> {args.output}")


if __name__ == "__main__":
    main()
