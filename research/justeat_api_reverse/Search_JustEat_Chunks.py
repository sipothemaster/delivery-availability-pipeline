import argparse
import csv
import os
import re

from playwright.sync_api import sync_playwright

from Scrape_JustEat import USER_AGENT, build_area_url, clean_postcode

DEFAULT_OUTPUT = "data/output/JustEat_chunk_search.csv"
PATTERNS = (
    "partnerlisting",
    "privatePlapi",
    "plapi",
    "searchByLocation",
    "restaurant-list",
    "open_now",
    "vertical",
    "filters",
    "postcode",
    "pageNumber",
    "pageSize",
    "offset",
    "serp",
    "discovery",
)


def clean(value):
    return re.sub(r"\s+", " ", value).strip()


def parse_args():
    parser = argparse.ArgumentParser(description="Search Just Eat JS chunks from browser context.")
    parser.add_argument("--postcode", default="ls42sw")
    parser.add_argument("--vertical", default="restaurants")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    url = build_area_url(clean_postcode(args.postcode), args.vertical, open_now=True)
    rows = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=args.headless)
        page = browser.new_page(
            viewport={"width": 1365, "height": 900},
            user_agent=USER_AGENT,
        )
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector('[data-qa="restaurant-card"]', timeout=15000)
        script_urls = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('script[src]'))
                .map(script => new URL(script.getAttribute('src'), location.href).href)
            """
        )
        script_urls = [
            script_url
            for script_url in script_urls
            if "/_next/static/chunks/" in script_url
        ]
        print(f"Found {len(script_urls)} chunk scripts")

        for script_url in script_urls:
            text = page.evaluate(
                """
                async (url) => {
                    const response = await fetch(url, { credentials: 'include' });
                    return await response.text();
                }
                """,
                script_url,
            )
            for pattern in PATTERNS:
                for match in re.finditer(re.escape(pattern), text, flags=re.IGNORECASE):
                    start = max(0, match.start() - 350)
                    end = min(len(text), match.end() + 650)
                    rows.append(
                        {
                            "script_url": script_url,
                            "script_name": script_url.rsplit("/", 1)[-1],
                            "pattern": pattern,
                            "position": match.start(),
                            "snippet": clean(text[start:end]),
                        }
                    )
            print(f"{script_url.rsplit('/', 1)[-1]} bytes={len(text)} matches_so_far={len(rows)}")

        browser.close()

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["script_url", "script_name", "pattern", "position", "snippet"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} matches -> {args.output}")


if __name__ == "__main__":
    main()
