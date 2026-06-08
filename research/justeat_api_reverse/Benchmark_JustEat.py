import argparse
import csv
import os
import time

from playwright.sync_api import sync_playwright

from Scrape_JustEat import (
    BASE_URL,
    USER_AGENT,
    build_area_url,
    clean_postcode,
    clean_text,
    count_loaded_cards,
    extract_expected_count,
    parse_rating,
    parse_verticals,
    scrape_loaded_cards,
)


DEFAULT_OUTPUT = "data/output/JustEat_scroll_benchmark.csv"
DEFAULT_SCENARIOS = (
    "2400:2.5",
    "5000:1.0",
    "8000:1.0",
)


def parse_scenario(value):
    scroll_pixels, delay = value.split(":", 1)
    return int(scroll_pixels), float(delay)


def extract_cards_in_browser(page, vertical=None):
    return page.evaluate(
        """
        ({ baseUrl, vertical }) => {
            const text = (node, selector) => {
                const target = node.querySelector(selector);
                return target ? target.innerText.replace(/\\s+/g, " ").trim() : null;
            };
            const attr = (node, selector, name) => {
                const target = node.querySelector(selector);
                return target ? target.getAttribute(name) : null;
            };
            return Array.from(document.querySelectorAll('[data-qa="restaurant-card"]'))
                .filter(card => !card.querySelector('[data-qa^="carousel-card-"]'))
                .map(card => {
                    const href = attr(card, "a[href*='/restaurants-']", "href");
                    return {
                        Name: text(card, '[data-qa="restaurant-info-name"]'),
                        Categories: text(card, '[data-qa="restaurant-cuisine"]'),
                        DeliveryTime: text(card, '[data-qa="restaurant-eta"]'),
                        DeliveryFee: text(card, '[data-qa="restaurant-delivery-fee"]'),
                        MinimumOrder: text(card, '[data-qa="restaurant-mov"]'),
                        Offer: text(card, '[data-qa="restaurant-offer"]'),
                        Tags: text(card, '[data-qa="restaurant-tags"]'),
                        RatingLabel: attr(card, '[data-qa="restaurant-ratings"]', "aria-label"),
                        Url: href ? new URL(href, baseUrl).href : null,
                        Vertical: vertical,
                    };
                })
                .filter(card => card.Name);
        }
        """,
        {"baseUrl": "https://www.just-eat.co.uk", "vertical": vertical},
    )


def safe_inner_text(card, selector):
    locator = card.locator(selector).first
    if locator.count() == 0:
        return None
    return clean_text(locator.inner_text(timeout=1000))


def scrape_loaded_cards_with_locators(page, vertical=None):
    restaurants = []
    cards = page.locator('[data-qa="restaurant-card"]')

    for index in range(cards.count()):
        card = cards.nth(index)
        if card.locator('[data-qa^="carousel-card-"]').count() > 0:
            continue

        name = safe_inner_text(card, '[data-qa="restaurant-info-name"]')
        if not name:
            continue

        link = card.locator("a[href*='/restaurants-']").first
        href = link.get_attribute("href") if link.count() else None
        rating_label = None
        rating_node = card.locator('[data-qa="restaurant-ratings"]').first
        if rating_node.count():
            rating_label = rating_node.get_attribute("aria-label")
        rating, review_count = parse_rating(rating_label)

        restaurants.append(
            {
                "Name": name,
                "Rating": rating,
                "ReviewCount": review_count,
                "Categories": safe_inner_text(card, '[data-qa="restaurant-cuisine"]'),
                "DeliveryTime": safe_inner_text(card, '[data-qa="restaurant-eta"]'),
                "DeliveryFee": safe_inner_text(card, '[data-qa="restaurant-delivery-fee"]'),
                "MinimumOrder": safe_inner_text(card, '[data-qa="restaurant-mov"]'),
                "Offer": safe_inner_text(card, '[data-qa="restaurant-offer"]'),
                "Tags": safe_inner_text(card, '[data-qa="restaurant-tags"]'),
                "Url": f"{BASE_URL}{href}" if href and href.startswith("/") else href,
                "Vertical": vertical,
            }
        )

    return restaurants


def timed(label, func):
    started_at = time.perf_counter()
    result = func()
    return result, time.perf_counter() - started_at


def run_scenario(page, url, vertical, scroll_pixels, delay, rounds):
    scenario_started_at = time.perf_counter()
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)

    expected_count, expected_seconds = timed("expected_count", lambda: extract_expected_count(page))
    rows = []
    previous_count = count_loaded_cards(page)
    print(
        f"Scenario scroll={scroll_pixels} delay={delay}s | expected={expected_count} | "
        f"initial_cards={previous_count}"
    )

    for round_number in range(1, rounds + 1):
        before_scroll_y = page.evaluate("window.scrollY")
        before_height = page.evaluate("document.body.scrollHeight")

        wheel_started_at = time.perf_counter()
        page.mouse.wheel(0, scroll_pixels)
        wheel_seconds = time.perf_counter() - wheel_started_at

        wait_started_at = time.perf_counter()
        page.wait_for_timeout(int(delay * 1000))
        wait_seconds = time.perf_counter() - wait_started_at

        count, count_seconds = timed("count_loaded_cards", lambda: count_loaded_cards(page))
        after_scroll_y = page.evaluate("window.scrollY")
        after_height = page.evaluate("document.body.scrollHeight")
        elapsed = time.perf_counter() - scenario_started_at

        row = {
            "vertical": vertical,
            "scroll_pixels": scroll_pixels,
            "delay_seconds": delay,
            "round": round_number,
            "expected_count": expected_count,
            "cards_before": previous_count,
            "cards_after": count,
            "new_cards": count - previous_count,
            "scroll_y_before": round(before_scroll_y),
            "scroll_y_after": round(after_scroll_y),
            "height_before": round(before_height),
            "height_after": round(after_height),
            "wheel_seconds": f"{wheel_seconds:.3f}",
            "wait_seconds": f"{wait_seconds:.3f}",
            "count_seconds": f"{count_seconds:.3f}",
            "elapsed_seconds": f"{elapsed:.3f}",
        }
        rows.append(row)
        previous_count = count
        print(
            f"  round {round_number}: cards={count} (+{row['new_cards']}) "
            f"scrollY={after_scroll_y:.0f}/{after_height:.0f} elapsed={elapsed:.1f}s"
        )

    locator_rows, locator_seconds = timed(
        "scrape_loaded_cards_with_locators",
        lambda: scrape_loaded_cards_with_locators(page, vertical=vertical),
    )
    fast_rows, fast_seconds = timed(
        "scrape_loaded_cards", lambda: scrape_loaded_cards(page, vertical=vertical)
    )
    print(
        f"  parse locator={len(locator_rows)} rows in {locator_seconds:.2f}s | "
        f"fast_js={len(fast_rows)} rows in {fast_seconds:.2f}s"
    )

    for row in rows:
        row["expected_seconds"] = f"{expected_seconds:.3f}"
        row["locator_parse_seconds"] = f"{locator_seconds:.3f}"
        row["browser_parse_seconds"] = f"{fast_seconds:.3f}"
        row["locator_rows"] = len(locator_rows)
        row["browser_rows"] = len(fast_rows)
        row["scenario_seconds"] = f"{time.perf_counter() - scenario_started_at:.3f}"

    return rows


def save_rows(rows, output_path):
    if not rows:
        return
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def parse_args():
    parser = argparse.ArgumentParser(description="Benchmark Just Eat scrolling behavior.")
    parser.add_argument("--postcode", default="ls42sw")
    parser.add_argument("--verticals", nargs="+", default=["restaurants"])
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--scenarios", nargs="+", default=list(DEFAULT_SCENARIOS))
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    postcode = clean_postcode(args.postcode)
    verticals = parse_verticals(args.verticals)
    scenarios = [parse_scenario(value) for value in args.scenarios]
    all_rows = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=args.headless)
        page = browser.new_page(
            viewport={"width": 1365, "height": 900},
            user_agent=USER_AGENT,
        )

        for vertical in verticals:
            url = build_area_url(postcode, vertical, open_now=True)
            for scroll_pixels, delay in scenarios:
                all_rows.extend(
                    run_scenario(page, url, vertical, scroll_pixels, delay, args.rounds)
                )

        browser.close()

    save_rows(all_rows, args.output)
    print(f"Saved benchmark rows: {len(all_rows)} -> {args.output}")


if __name__ == "__main__":
    main()
