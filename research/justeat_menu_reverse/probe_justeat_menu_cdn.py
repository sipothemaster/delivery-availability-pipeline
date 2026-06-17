import argparse
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


CDN_BASE = "https://menu-globalmenucdn.je-apis.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def fetch_json(url):
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.status, json.load(response)


def probe_slug(slug):
    paths = {
        "manifest": f"/{slug}_uk_manifest.json",
        "manifest_v2_2": f"/v2_2/{slug}_uk_manifest.json",
        "items": f"/{slug}_uk_items.json",
        "item_details": f"/{slug}_uk_itemDetails.json",
    }

    result = {"slug": slug, "requests": {}}
    for label, path in paths.items():
        url = f"{CDN_BASE}{path}"
        try:
            status, payload = fetch_json(url)
            result["requests"][label] = {
                "status": status,
                "url": url,
                "top_keys": list(payload.keys())[:30] if isinstance(payload, dict) else [],
                "restaurant_id": payload.get("RestaurantId") if isinstance(payload, dict) else "",
                "item_count": len(payload.get("Items") or []) if isinstance(payload, dict) else 0,
                "modifier_group_count": len(payload.get("ModifierGroups") or []) if isinstance(payload, dict) else 0,
            }
            if label == "manifest":
                info = payload.get("RestaurantInfo") or {}
                result["manifest_summary"] = {
                    "restaurant_id": payload.get("RestaurantId"),
                    "restaurant_name": info.get("Name"),
                    "timezone": info.get("TimeZone"),
                    "is_offline": info.get("IsOffline"),
                    "opening_time_service_types": [
                        item.get("ServiceType")
                        for item in (info.get("RestaurantOpeningTimes") or [])
                    ],
                    "menu_count": len(payload.get("Menus") or []),
                    "items_url": payload.get("ItemsUrl"),
                    "item_details_url": payload.get("ItemDetailsUrl"),
                }
        except HTTPError as exc:
            result["requests"][label] = {"status": exc.code, "url": url}
    return result


def parse_args():
    parser = argparse.ArgumentParser(
        description="Probe Just Eat menu CDN endpoints from restaurant slugs."
    )
    parser.add_argument("slugs", nargs="+")
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    return parser.parse_args()


def main():
    args = parse_args()
    for index, slug in enumerate(args.slugs):
        print(json.dumps(probe_slug(slug), ensure_ascii=False, indent=2))
        if index < len(args.slugs) - 1:
            time.sleep(args.delay_seconds)


if __name__ == "__main__":
    main()
