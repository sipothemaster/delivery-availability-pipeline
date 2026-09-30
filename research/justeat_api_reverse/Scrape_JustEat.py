import argparse
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.just-eat.co.uk"
JUST_EAT_API_URL = "https://uk.api.just-eat.io"
DEFAULT_POSTCODE = "ls42sw"
DEFAULT_OUTPUT = "data/output/JustEat_ls42sw_sample.csv"
DEFAULT_VERTICALS = ("restaurants", "groceries")
VALID_VERTICALS = {"restaurants", "groceries"}
AREA_SLUGS = {
    "ls42sw": "burley",
}
USER_AGENT = (
    "DFRE-DeliveryAvailabilityResearch/1.0 "
    "(+https://github.com/sipothemaster/delivery-availability-pipeline)"
)


def clean_postcode(postcode):
    return re.sub(r"\s+", "", str(postcode)).lower()


def parse_verticals(verticals):
    if isinstance(verticals, str):
        verticals = re.split(r"[\s,]+", verticals)
    parsed = []
    for vertical in verticals:
        vertical = clean_text(vertical)
        if not vertical:
            continue
        vertical = vertical.lower()
        if vertical == "grocery":
            vertical = "groceries"
        if vertical not in VALID_VERTICALS:
            raise ValueError(
                f"Unsupported Just Eat vertical '{vertical}'. "
                f"Use one of: {', '.join(sorted(VALID_VERTICALS))}."
            )
        parsed.append(vertical)
    return parsed or list(DEFAULT_VERTICALS)


def build_area_url(postcode, vertical, open_now=True, area_slug=None):
    slug = area_slug if area_slug is not None else AREA_SLUGS.get(postcode)
    area_path = f"{postcode}-{slug}" if slug else postcode
    filters = [f"vertical={vertical}"]
    if open_now:
        filters.append("filter=open_now")
    return f"{BASE_URL}/area/{area_path}?{'&'.join(filters)}"


def build_area_path(postcode, area_slug=None):
    slug = area_slug if area_slug is not None else AREA_SLUGS.get(postcode)
    return f"{postcode}-{slug}" if slug else postcode


def build_listing_api_url(postcode, area_slug=None):
    area_path = build_area_path(postcode, area_slug=area_slug)
    query = urlencode({"serviceType": "delivery"})
    return f"{JUST_EAT_API_URL}/discovery/uk/restaurants/enriched/byslug/{area_path}?{query}"


def parse_rating(aria_label):
    if not aria_label:
        return None, None

    match = re.search(r"([\d.]+)\s+stars?\s+from\s+([\d,]+)\s+reviews?", aria_label)
    if not match:
        return None, None

    rating = float(match.group(1))
    review_count = int(match.group(2).replace(",", ""))
    return rating, review_count


def clean_text(value):
    if value is None:
        return None
    return re.sub(r"\s+", " ", value).strip()


def format_money(value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return clean_text(value)
    if value <= 0:
        return "Free"
    return f"£{value:.2f}"


def format_eta(eta):
    if not isinstance(eta, dict):
        return None
    lower = eta.get("rangeLower")
    upper = eta.get("rangeUpper")
    approximate = eta.get("approximate")
    if lower is not None and upper is not None:
        return f"{lower}-{upper} mins"
    if approximate is not None:
        return f"{approximate} mins"
    return None


def restaurant_url_from_api(restaurant):
    unique_name = restaurant.get("uniqueName")
    restaurant_id = restaurant.get("id")
    if unique_name and restaurant_id:
        return f"{BASE_URL}/restaurants-{unique_name}/menu"
    return None


def fetch_listing_api(postcode, area_slug=None):
    url = build_listing_api_url(postcode, area_slug=area_slug)
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Referer": f"{BASE_URL}/area/{build_area_path(postcode, area_slug=area_slug)}",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8")), url
    except HTTPError as exc:
        raise RuntimeError(f"Just Eat API returned HTTP {exc.code}: {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Just Eat API: {exc}") from exc


def api_restaurant_to_row(restaurant, grocery_ids=None):
    cuisines = restaurant.get("cuisines") or []
    cuisine_names = [item.get("name") for item in cuisines if item.get("name")]
    cuisine_ids = {
        item.get("uniqueName") for item in cuisines if item.get("uniqueName")
    }
    deals = restaurant.get("deals") or []
    deal_text = [
        deal.get("description") for deal in deals if clean_text(deal.get("description"))
    ]
    rating = restaurant.get("rating") or {}
    restaurant_id = (
        str(restaurant.get("id")) if restaurant.get("id") is not None else None
    )
    vertical = (
        "groceries" if grocery_ids and restaurant_id in grocery_ids else "restaurants"
    )

    return {
        "Name": restaurant.get("name"),
        "Rating": rating.get("starRating"),
        "ReviewCount": rating.get("count"),
        "Categories": ", ".join(cuisine_names) if cuisine_names else None,
        "DeliveryTime": format_eta(restaurant.get("deliveryEtaMinutes")),
        "DeliveryFee": format_money(restaurant.get("deliveryCost")),
        "MinimumOrder": format_money(restaurant.get("minimumDeliveryValue")),
        "Offer": "; ".join(deal_text) if deal_text else None,
        "Tags": ", ".join(restaurant.get("tags") or []) or None,
        "Url": restaurant_url_from_api(restaurant),
        "Vertical": vertical,
        "JustEatId": restaurant_id,
        "CuisineIds": ", ".join(sorted(cuisine_ids)) if cuisine_ids else None,
    }


def JustEat_api_scraper(
    postcode=DEFAULT_POSTCODE,
    output_path=DEFAULT_OUTPUT,
    max_restaurants=None,
    area_slug=None,
):
    postcode = clean_postcode(postcode)
    started_at = time.perf_counter()
    data, url = fetch_listing_api(postcode, area_slug=area_slug)
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
    if max_restaurants:
        open_restaurants = open_restaurants[:max_restaurants]

    rows = [
        api_restaurant_to_row(restaurant, grocery_ids)
        for restaurant in open_restaurants
    ]
    print(f"Opened API {url}")
    print(f"API returned {len(restaurants)} restaurants")
    print(f"Open now for delivery: {len(rows)}")
    print(f"Total Just Eat API scrape time: {time.perf_counter() - started_at:.1f}s")
    return save_restaurants(rows, output_path)


def safe_inner_text(card, selector):
    locator = card.locator(selector).first
    try:
        if locator.count() == 0:
            return None
        return clean_text(locator.inner_text(timeout=1000))
    except PlaywrightTimeoutError:
        return None


def scrape_loaded_cards(page, vertical=None):
    restaurants = page.evaluate(
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
                        RatingLabel: attr(card, '[data-qa="restaurant-ratings"]', "aria-label"),
                        Categories: text(card, '[data-qa="restaurant-cuisine"]'),
                        DeliveryTime: text(card, '[data-qa="restaurant-eta"]'),
                        DeliveryFee: text(card, '[data-qa="restaurant-delivery-fee"]'),
                        MinimumOrder: text(card, '[data-qa="restaurant-mov"]'),
                        Offer: text(card, '[data-qa="restaurant-offer"]'),
                        Tags: text(card, '[data-qa="restaurant-tags"]'),
                        Url: href ? new URL(href, baseUrl).href : null,
                        Vertical: vertical,
                    };
                })
                .filter(card => card.Name);
        }
        """,
        {"baseUrl": BASE_URL, "vertical": vertical},
    )

    for restaurant in restaurants:
        rating, review_count = parse_rating(restaurant.pop("RatingLabel", None))
        restaurant["Rating"] = rating
        restaurant["ReviewCount"] = review_count

    return restaurants


def extract_expected_count(page):
    body_text = page.locator("body").inner_text(timeout=5000)
    match = re.search(r"(\d[\d,]*)\s+places", body_text, re.IGNORECASE)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def count_loaded_cards(page):
    return page.evaluate("""
        () => Array.from(document.querySelectorAll('[data-qa="restaurant-card"]'))
            .filter(card => !card.querySelector('[data-qa^="carousel-card-"]'))
            .length
        """)


def save_restaurants(restaurants, output_path):
    df = pd.DataFrame(restaurants)
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    return df


def JustEat_scraper(
    postcode=DEFAULT_POSTCODE,
    output_path=DEFAULT_OUTPUT,
    max_restaurants=None,
    scroll_delay=1.0,
    scroll_pixels=8000,
    idle_rounds=8,
    headless=False,
    checkpoint_every=0,
    verticals=DEFAULT_VERTICALS,
    open_now=True,
    area_slug=None,
    initial_delay=1.0,
):
    postcode = clean_postcode(postcode)
    verticals = parse_verticals(verticals)
    started_at = time.perf_counter()
    all_restaurants = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=headless)
        page = browser.new_page(
            viewport={"width": 1365, "height": 900},
            user_agent=USER_AGENT,
        )

        for vertical in verticals:
            url = build_area_url(
                postcode, vertical, open_now=open_now, area_slug=area_slug
            )
            vertical_started_at = time.perf_counter()

            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector('[data-qa="restaurant-card"]', timeout=15000)
            page.wait_for_timeout(int(initial_delay * 1000))

            title = page.title()
            if "Attention Required" in title or "Cloudflare" in title:
                browser.close()
                raise RuntimeError(
                    "Just Eat returned a Cloudflare challenge. Re-run without --headless "
                    "and complete any visible browser check before scraping."
                )

            expected_count = extract_expected_count(page)
            print(f"Opened {url}")
            if expected_count:
                print(f"Page says: {expected_count} places")

            previous_count = 0
            previous_scroll_y = -1
            stalled_rounds = 0
            scroll_round = 0

            while True:
                scroll_round += 1
                current_count = count_loaded_cards(page)
                scroll_y = page.evaluate("window.scrollY")
                scroll_height = page.evaluate("document.body.scrollHeight")
                elapsed = time.perf_counter() - vertical_started_at
                print(
                    f"[{vertical}] Loaded {current_count} cards | "
                    f"scrollY={scroll_y:.0f}/{scroll_height:.0f} | "
                    f"elapsed={elapsed:.1f}s",
                    flush=True,
                )

                if max_restaurants and current_count >= max_restaurants:
                    break
                if expected_count and current_count >= expected_count:
                    break

                if current_count == previous_count and scroll_y == previous_scroll_y:
                    stalled_rounds += 1
                else:
                    stalled_rounds = 0
                    previous_count = current_count
                    previous_scroll_y = scroll_y

                if stalled_rounds >= idle_rounds:
                    break

                if checkpoint_every and scroll_round % checkpoint_every == 0:
                    checkpoint = all_restaurants + scrape_loaded_cards(
                        page, vertical=vertical
                    )
                    if max_restaurants:
                        checkpoint = checkpoint[:max_restaurants]
                    save_restaurants(checkpoint, output_path)
                    print(f"Checkpoint saved: {len(checkpoint)} rows", flush=True)

                page.mouse.wheel(0, scroll_pixels)
                page.wait_for_timeout(int(scroll_delay * 1000))

            restaurants = scrape_loaded_cards(page, vertical=vertical)
            all_restaurants.extend(restaurants)
            print(
                f"[{vertical}] Finished {len(restaurants)} rows in "
                f"{time.perf_counter() - vertical_started_at:.1f}s",
                flush=True,
            )

        browser.close()

    if max_restaurants:
        all_restaurants = all_restaurants[:max_restaurants]
    print(f"Total Just Eat scrape time: {time.perf_counter() - started_at:.1f}s")
    return save_restaurants(all_restaurants, output_path)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape Just Eat restaurant listings for one postcode."
    )
    parser.add_argument("--postcode", default=DEFAULT_POSTCODE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--use-api",
        action="store_true",
        help="Use Just Eat's public discovery API and locally keep open delivery partners.",
    )
    parser.add_argument(
        "--max-restaurants",
        type=int,
        help="Stop after this many unique restaurants. Useful for samples.",
    )
    parser.add_argument("--scroll-delay", type=float, default=1.0)
    parser.add_argument("--scroll-pixels", type=int, default=8000)
    parser.add_argument("--initial-delay", type=float, default=1.0)
    parser.add_argument("--idle-rounds", type=int, default=8)
    parser.add_argument(
        "--verticals",
        nargs="+",
        default=list(DEFAULT_VERTICALS),
        help="Just Eat verticals to scrape, e.g. restaurants groceries.",
    )
    parser.add_argument(
        "--include-closed",
        action="store_true",
        help="Do not add the open_now filter.",
    )
    parser.add_argument(
        "--area-slug",
        help="Optional Just Eat area slug after the postcode, e.g. burley.",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=0,
        help="Save a partial CSV after every N scroll rounds.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless. Just Eat may block this with Cloudflare.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.use_api:
        JustEat_api_scraper(
            postcode=args.postcode,
            output_path=args.output,
            max_restaurants=args.max_restaurants,
            area_slug=args.area_slug,
        )
    else:
        JustEat_scraper(
            postcode=args.postcode,
            output_path=args.output,
            max_restaurants=args.max_restaurants,
            scroll_delay=args.scroll_delay,
            scroll_pixels=args.scroll_pixels,
            idle_rounds=args.idle_rounds,
            headless=args.headless,
            checkpoint_every=args.checkpoint_every,
            verticals=args.verticals,
            open_now=not args.include_closed,
            area_slug=args.area_slug,
            initial_delay=args.initial_delay,
        )
