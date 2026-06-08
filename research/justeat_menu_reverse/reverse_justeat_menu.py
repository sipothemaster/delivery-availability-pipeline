import argparse
import csv
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


DEFAULT_CDN_BASE = "https://menu-globalmenucdn.je-apis.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_nested(data, keys, default=None):
    node = data
    for key in keys:
        if isinstance(node, dict):
            if key not in node:
                return default
            node = node[key]
        elif isinstance(node, list) and isinstance(key, int):
            if key < 0 or key >= len(node):
                return default
            node = node[key]
        else:
            return default
    return node


def extract_next_data(html):
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        raise ValueError("Could not find __NEXT_DATA__ in restaurant page HTML")
    return json.loads(script.string)


def extract_menu_payload(next_data):
    app_props = get_nested(next_data, ["props", "appProps"], {})
    cdn_base = get_nested(app_props, ["clientConfig", "jeRestaurantCdnUrls", "uk"], DEFAULT_CDN_BASE)
    menu_cdn = get_nested(app_props, ["preloadedState", "menu", "restaurant", "cdn"], {})
    restaurant = menu_cdn.get("restaurant") or {}
    info = restaurant.get("restaurantInfo") or {}
    return {
        "cdn_base": (cdn_base or DEFAULT_CDN_BASE).rstrip("/"),
        "restaurant": restaurant,
        "inline_items": menu_cdn.get("items") or [],
        "restaurant_id": str(restaurant.get("restaurantId") or info.get("id") or ""),
        "restaurant_name": info.get("name") or "",
        "items_url": restaurant.get("itemsUrl") or "",
        "item_details_url": restaurant.get("itemDetailsUrl") or "",
        "truncated_url": restaurant.get("truncatedUrl") or "",
    }


def fetch_json(url):
    response = requests.get(
        url,
        timeout=30,
        headers={"accept": "application/json", "user-agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.json()


def fetch_page(url):
    response = requests.get(
        url,
        timeout=30,
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "user-agent": USER_AGENT,
        },
    )
    response.raise_for_status()
    return response.text


def price_range(item):
    prices = []
    for variation in item.get("Variations") or []:
        price = variation.get("BasePrice")
        if isinstance(price, (int, float)):
            prices.append(price)
    if not prices:
        return "", ""
    return min(prices), max(prices)


def json_cell(value):
    if value in (None, "", [], {}):
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def collect_menu_rows(payload, items_payload, details_payload):
    restaurant = payload["restaurant"]
    menus = restaurant.get("menus") or []
    items = items_payload.get("Items") or payload["inline_items"] or []
    items_by_id = {item.get("Id"): item for item in items if isinstance(item, dict)}

    rows = []
    missing = 0
    for menu_index, menu in enumerate(menus):
        menu_id = menu.get("menuGroupId") or ""
        menu_name = menu.get("description") or ""
        for category_index, category in enumerate(menu.get("categories") or []):
            item_ids = category.get("itemIds") or []
            for position, item_id in enumerate(item_ids):
                item = items_by_id.get(item_id)
                if not item:
                    missing += 1
                    continue
                min_price, max_price = price_range(item)
                variations = item.get("Variations") or []
                rows.append(
                    {
                        "restaurant_id": payload["restaurant_id"],
                        "restaurant_name": payload["restaurant_name"],
                        "menu_index": menu_index,
                        "menu_id": menu_id,
                        "menu_name": menu_name,
                        "category_index": category_index,
                        "category_id": category.get("id") or "",
                        "category_name": category.get("name") or "",
                        "position_in_category": position,
                        "item_id": item.get("Id") or "",
                        "item_name": item.get("Name") or "",
                        "item_description": item.get("Description") or "",
                        "item_type": item.get("Type") or "",
                        "labels": "|".join(item.get("Labels") or []),
                        "min_price": min_price,
                        "max_price": max_price,
                        "variation_ids": "|".join(str(v.get("Id") or "") for v in variations),
                        "variation_count": len(variations),
                        "modifier_group_ids": "|".join(
                            sorted(
                                {
                                    str(group_id)
                                    for variation in variations
                                    for group_id in (variation.get("ModifierGroupsIds") or [])
                                }
                            )
                        ),
                        "deal_group_ids": "|".join(
                            sorted(
                                {
                                    str(group_id)
                                    for variation in variations
                                    for group_id in (variation.get("DealGroupsIds") or [])
                                }
                            )
                        ),
                        "energy_display": get_nested(item, ["EnergyContent", "EnergyDisplay"], ""),
                        "energy_units": item.get("EnergyUnits") or "",
                        "image_0": get_nested(item, ["ImageSources", 0, "Path"], "")
                        if isinstance(item.get("ImageSources"), list)
                        else "",
                        "restrictions_json": json_cell(item.get("Restrictions")),
                    }
                )
    return rows, missing


def collect_modifier_rows(payload, details_payload):
    modifier_sets = {
        str(item.get("Id")): item.get("Modifier") or {}
        for item in details_payload.get("ModifierSets") or []
        if isinstance(item, dict)
    }
    rows = []
    for group in details_payload.get("ModifierGroups") or []:
        modifier_ids = group.get("Modifiers") or []
        for position, modifier_key in enumerate(modifier_ids):
            modifier = modifier_sets.get(str(modifier_key), {})
            rows.append(
                {
                    "restaurant_id": payload["restaurant_id"],
                    "restaurant_name": payload["restaurant_name"],
                    "modifier_group_id": group.get("Id") or "",
                    "modifier_group_name": group.get("Name") or "",
                    "group_min_choices": group.get("MinChoices", ""),
                    "group_max_choices": group.get("MaxChoices", ""),
                    "position_in_group": position,
                    "modifier_set_id": modifier_key,
                    "modifier_id": modifier.get("Id") or "",
                    "modifier_name": modifier.get("Name") or "",
                    "addition_price": modifier.get("AdditionPrice", ""),
                    "remove_price": modifier.get("RemovePrice", ""),
                    "default_choices": modifier.get("DefaultChoices", ""),
                    "modifier_min_choices": modifier.get("MinChoices", ""),
                    "modifier_max_choices": modifier.get("MaxChoices", ""),
                    "nutrition_json": json_cell(modifier.get("NutritionalInfo")),
                }
            )
    return rows


def collect_deal_rows(payload, details_payload, items_payload):
    variation_index = {}
    for item in items_payload.get("Items") or payload["inline_items"] or []:
        if not isinstance(item, dict):
            continue
        for variation in item.get("Variations") or []:
            variation_index[str(variation.get("Id"))] = (item, variation)

    rows = []
    for group in details_payload.get("DealGroups") or []:
        variations = group.get("DealItemVariations") or []
        for position, variation in enumerate(variations):
            deal_variation_id = str(variation.get("DealItemVariationId") or "")
            item, item_variation = variation_index.get(deal_variation_id, ({}, {}))
            rows.append(
                {
                    "restaurant_id": payload["restaurant_id"],
                    "restaurant_name": payload["restaurant_name"],
                    "deal_group_id": group.get("Id") or "",
                    "deal_group_name": group.get("Name") or "",
                    "number_of_choices": group.get("NumberOfChoices", ""),
                    "position_in_group": position,
                    "deal_item_variation_id": deal_variation_id,
                    "item_id": item.get("Id") or "",
                    "item_name": item.get("Name") or "",
                    "variation_name": item_variation.get("Name") or "",
                    "base_price": item_variation.get("BasePrice", ""),
                    "modifier_group_ids": "|".join(item_variation.get("ModifierGroupsIds") or []),
                    "min_choices": variation.get("MinChoices", ""),
                    "max_choices": variation.get("MaxChoices", ""),
                    "addition_price": variation.get("AdditionPrice", ""),
                    "servings_display": get_nested(variation, ["NumberOfServings", "ServingsDisplay"], ""),
                }
            )
    return rows


def write_csv(path, rows, fieldnames):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def source_name(source):
    parsed = urlparse(source)
    if parsed.scheme and parsed.path:
        return Path(parsed.path).stem or "restaurant"
    return Path(source).stem


def process_source(source, output_dir):
    html = fetch_page(source) if source.startswith(("http://", "https://")) else read_text(source)
    next_data = extract_next_data(html)
    payload = extract_menu_payload(next_data)
    stem = payload["restaurant_id"] or source_name(source)

    restaurant_json = output_dir / f"{stem}_restaurant_nextdata.json"
    write_json(restaurant_json, payload["restaurant"])

    items_payload = {"Items": payload["inline_items"]}
    if payload["items_url"]:
        items_url = f"{payload['cdn_base']}/{payload['items_url']}"
        items_payload = fetch_json(items_url)
        write_json(output_dir / f"{stem}_items_cdn.json", items_payload)

    details_payload = {"ModifierGroups": [], "DealGroups": [], "ModifierSets": []}
    if payload["item_details_url"]:
        details_url = f"{payload['cdn_base']}/{payload['item_details_url']}"
        details_payload = fetch_json(details_url)
        write_json(output_dir / f"{stem}_itemDetails_cdn.json", details_payload)

    item_rows, missing = collect_menu_rows(payload, items_payload, details_payload)
    modifier_rows = collect_modifier_rows(payload, details_payload)
    deal_rows = collect_deal_rows(payload, details_payload, items_payload)
    summary = {
        "source": source,
        "restaurant_id": payload["restaurant_id"],
        "restaurant_name": payload["restaurant_name"],
        "cdn_base": payload["cdn_base"],
        "items_url": payload["items_url"],
        "item_details_url": payload["item_details_url"],
        "truncated_url": payload["truncated_url"],
        "menus": len(payload["restaurant"].get("menus") or []),
        "items": len(items_payload.get("Items") or []),
        "missing_item_details": missing,
        "modifier_groups": len(details_payload.get("ModifierGroups") or []),
        "modifier_options": len(modifier_rows),
        "deal_groups": len(details_payload.get("DealGroups") or []),
        "deal_options": len(deal_rows),
    }
    return summary, item_rows, modifier_rows, deal_rows


def parse_args():
    parser = argparse.ArgumentParser(description="Reverse Just Eat restaurant menu data sources.")
    parser.add_argument("sources", nargs="+", help="Restaurant page URLs or saved HTML files.")
    parser.add_argument("--output-dir", default="data/output/restaurant_menu_reverse")
    return parser.parse_args()


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    all_items = []
    all_modifiers = []
    all_deals = []
    for source in args.sources:
        summary, item_rows, modifier_rows, deal_rows = process_source(source, output_dir)
        summaries.append(summary)
        all_items.extend(item_rows)
        all_modifiers.extend(modifier_rows)
        all_deals.extend(deal_rows)
        print(
            f"{summary['restaurant_id']} {summary['restaurant_name']}: "
            f"{summary['items']} items, {summary['modifier_groups']} modifier groups, "
            f"{summary['deal_groups']} deal groups"
        )

    write_csv(output_dir / "summary.csv", summaries, list(summaries[0].keys()))
    if all_items:
        write_csv(output_dir / "menu_items.csv", all_items, list(all_items[0].keys()))
    if all_modifiers:
        write_csv(output_dir / "modifier_options.csv", all_modifiers, list(all_modifiers[0].keys()))
    if all_deals:
        write_csv(output_dir / "deal_options.csv", all_deals, list(all_deals[0].keys()))


if __name__ == "__main__":
    main()
