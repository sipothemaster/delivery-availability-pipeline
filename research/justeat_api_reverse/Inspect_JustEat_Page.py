import argparse
import csv
import json
import os
import re
import time

from playwright.sync_api import sync_playwright

from Scrape_JustEat import USER_AGENT, build_area_url, clean_postcode

DEFAULT_OUTPUT = "data/output/JustEat_page_scripts.csv"


def clean_text(value):
    return re.sub(r"\s+", " ", value).strip()


def parse_args():
    parser = argparse.ArgumentParser(description="Inspect Just Eat page embedded data.")
    parser.add_argument("--postcode", default="ls42sw")
    parser.add_argument("--vertical", default="restaurants")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    postcode = clean_postcode(args.postcode)
    url = build_area_url(postcode, args.vertical, open_now=True)
    rows = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=args.headless)
        page = browser.new_page(
            viewport={"width": 1365, "height": 900},
            user_agent=USER_AGENT,
        )
        started_at = time.perf_counter()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector('[data-qa="restaurant-card"]', timeout=15000)
        page.wait_for_timeout(1000)

        scripts = page.locator("script")
        print(f"Opened {url} in {time.perf_counter() - started_at:.1f}s")
        print(f"script count: {scripts.count()}")

        for index in range(scripts.count()):
            script = scripts.nth(index)
            src = script.get_attribute("src")
            script_id = script.get_attribute("id")
            script_type = script.get_attribute("type")
            text = script.inner_text(timeout=1000) if not src else ""
            sample = clean_text(text[:1200]) if text else ""
            has_restaurant = "restaurant" in sample.lower()
            has_next_data = script_id == "__NEXT_DATA__"
            json_keys = None
            if text.lstrip().startswith(("{", "[")):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        json_keys = ",".join(list(parsed.keys())[:30])
                except Exception:
                    pass
            if src or sample or has_next_data:
                rows.append(
                    {
                        "index": index,
                        "id": script_id,
                        "type": script_type,
                        "src": src,
                        "text_length": len(text),
                        "has_restaurant_sample": has_restaurant,
                        "json_keys": json_keys,
                        "sample": sample,
                    }
                )
                print(
                    f"{index}: id={script_id} type={script_type} src={src} "
                    f"text={len(text)} restaurant_sample={has_restaurant} json_keys={json_keys}"
                )

        browser.close()

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} script rows -> {args.output}")


if __name__ == "__main__":
    main()
