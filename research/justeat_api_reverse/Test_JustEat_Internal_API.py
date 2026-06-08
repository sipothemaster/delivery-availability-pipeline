import argparse
import json
import os
import time
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

from Scrape_JustEat import USER_AGENT, build_area_url, clean_postcode

DEFAULT_OUTPUT = "data/output/JustEat_internal_api_test.json"


def get_path(data, path, default=None):
    node = data
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def find_restaurant_lists(node, path="$", findings=None):
    if findings is None:
        findings = []
    if isinstance(node, list):
        hits = 0
        for item in node[:30]:
            if not isinstance(item, dict):
                continue
            keys = {str(key).lower() for key in item.keys()}
            text = json.dumps(item, ensure_ascii=False)[:1500].lower()
            if "name" in keys and ("restaurant" in text or "delivery" in text or "rating" in text):
                hits += 1
        if hits:
            findings.append({"path": path, "length": len(node), "hits_first_30": hits})
        for index, item in enumerate(node[:5]):
            find_restaurant_lists(item, f"{path}[{index}]", findings)
    elif isinstance(node, dict):
        for key, item in node.items():
            find_restaurant_lists(item, f"{path}.{key}", findings)
    return findings


def parse_args():
    parser = argparse.ArgumentParser(description="Try Just Eat internal listing API candidates.")
    parser.add_argument("--postcode", default="ls42sw")
    parser.add_argument("--vertical", default="restaurants")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    postcode = clean_postcode(args.postcode)
    page_url = build_area_url(postcode, args.vertical, open_now=True)
    results = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=args.headless)
        page = browser.new_page(
            viewport={"width": 1365, "height": 900},
            user_agent=USER_AGENT,
        )
        page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#__NEXT_DATA__", state="attached", timeout=15000)
        next_data = json.loads(page.locator("#__NEXT_DATA__").inner_text(timeout=5000))
        private_plapi = get_path(next_data, ["props", "appProps", "clientConfig", "privatePlapi", "uk"])
        public_api = "https://uk.api.just-eat.io"
        bases = [base for base in (public_api, private_plapi) if base]
        slug = f"{postcode}-burley" if postcode == "ls42sw" else postcode
        query_variants = [
            {"serviceType": "delivery"},
            {"serviceType": "delivery", "availability": "open_now"},
            {"serviceType": "delivery", "vertical": args.vertical},
            {"serviceType": "delivery", "vertical": args.vertical, "availability": "open_now"},
            {"serviceType": "delivery", "filter": "open_now"},
            {"serviceType": "delivery", "vertical": args.vertical, "filter": "open_now"},
            {"serviceType": "delivery", "limit": "50"},
            {"serviceType": "delivery", "limit": "300"},
            {"serviceType": "delivery", "availability": "open_now", "limit": "300"},
        ]
        path_variants = [
            f"/discovery/uk/restaurants/enriched/byslug/{slug}",
            f"/discovery/uk/restaurants/enriched/bypostcode/{postcode}",
            f"/discovery/gb/restaurants/enriched/byslug/{slug}",
            f"/discovery/gb/restaurants/enriched/bypostcode/{postcode}",
        ]

        for base in bases:
            for path in path_variants:
                for params in query_variants:
                    url = f"{base.rstrip('/')}{path}?{urlencode(params)}"
                    started_at = time.perf_counter()
                    try:
                        response = page.context.request.get(
                            url,
                            headers={
                                "accept": "application/json, text/plain, */*",
                                "referer": page_url,
                                "origin": "https://www.just-eat.co.uk",
                                "user-agent": USER_AGENT,
                                "x-je-application-id": "7",
                            },
                            timeout=15000,
                        )
                        result = {
                            "ok": response.ok,
                            "status": response.status,
                            "statusText": response.status_text,
                            "contentType": response.headers.get("content-type"),
                            "text": response.text(),
                        }
                    except Exception as exc:
                        result = {
                            "ok": False,
                            "status": 0,
                            "error": f"{type(exc).__name__}: {exc}",
                            "text": "",
                        }
                    elapsed = time.perf_counter() - started_at
                    parsed = None
                    findings = []
                    try:
                        parsed = json.loads(result.get("text") or "")
                        findings = find_restaurant_lists(parsed)
                    except Exception:
                        pass
                    row = {
                        "url": url,
                        "status": result.get("status"),
                        "ok": result.get("ok"),
                        "content_type": result.get("contentType"),
                        "error": result.get("error"),
                        "elapsed_seconds": round(elapsed, 3),
                        "text_sample": (result.get("text") or "")[:500],
                        "restaurant_lists": findings[:10],
                    }
                    results.append(row)
                    print(
                        f"{row['status']} ok={row['ok']} lists={len(findings)} "
                        f"{elapsed:.2f}s {url}"
                    )

        browser.close()

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print(f"Saved {len(results)} API attempts -> {args.output}")


if __name__ == "__main__":
    main()
