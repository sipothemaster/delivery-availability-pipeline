import argparse
import json
import os
import re

from playwright.sync_api import sync_playwright

from Scrape_JustEat import USER_AGENT, build_area_url, clean_postcode

DEFAULT_OUTPUT = "data/output/JustEat_nextdata_paths.json"


def restaurant_score(item):
    if not isinstance(item, dict):
        return 0
    keys = {str(key).lower() for key in item.keys()}
    score = 0
    for token in (
        "name",
        "restaurantname",
        "rating",
        "cuisines",
        "deliveryeta",
        "deliverytime",
        "deliveryfee",
        "minimumorder",
        "url",
        "seo",
        "availability",
    ):
        if token in keys:
            score += 1
    text = json.dumps(item, ensure_ascii=False)[:2000].lower()
    for token in ("restaurant", "delivery", "rating", "reviews"):
        if token in text:
            score += 1
    return score


def walk(node, path="$", findings=None):
    if findings is None:
        findings = []
    if isinstance(node, list):
        scores = [restaurant_score(item) for item in node[:20]]
        strong = sum(1 for score in scores if score >= 3)
        if len(node) >= 5 and strong:
            first_dict = next((item for item in node if isinstance(item, dict)), {})
            findings.append(
                {
                    "path": path,
                    "length": len(node),
                    "strong_first_20": strong,
                    "first_keys": list(first_dict.keys())[:50],
                    "first_sample": first_dict,
                }
            )
        for index, item in enumerate(node[:5]):
            walk(item, f"{path}[{index}]", findings)
    elif isinstance(node, dict):
        for key, item in node.items():
            walk(item, f"{path}.{key}", findings)
    return findings


def collect_keyword_paths(node, path="$", findings=None):
    if findings is None:
        findings = []
    keywords = ("restaurant", "restaurants", "place", "places", "serp", "listing", "cuisine")
    if isinstance(node, dict):
        for key, item in node.items():
            item_path = f"{path}.{key}"
            key_lower = str(key).lower()
            if any(keyword in key_lower for keyword in keywords):
                findings.append(
                    {
                        "path": item_path,
                        "kind": type(item).__name__,
                        "size": len(item) if isinstance(item, (dict, list, str)) else None,
                        "sample": simplify_sample(item),
                    }
                )
            collect_keyword_paths(item, item_path, findings)
    elif isinstance(node, list):
        if len(node) >= 5:
            first = next((item for item in node if isinstance(item, dict)), node[0] if node else None)
            findings.append(
                {
                    "path": path,
                    "kind": "list",
                    "size": len(node),
                    "sample": simplify_sample(first),
                }
            )
        for index, item in enumerate(node[:10]):
            collect_keyword_paths(item, f"{path}[{index}]", findings)
    elif isinstance(node, str):
        value = node.lower()
        if any(keyword in value for keyword in keywords):
            findings.append(
                {
                    "path": path,
                    "kind": "str",
                    "size": len(node),
                    "sample": simplify_sample(node),
                }
            )
    return findings


def simplify_sample(value):
    if isinstance(value, dict):
        return {
            key: simplify_sample(item)
            for key, item in list(value.items())[:25]
            if not isinstance(item, (dict, list)) or key.lower() in {"rating", "cuisines"}
        }
    if isinstance(value, list):
        return [simplify_sample(item) for item in value[:3]]
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()[:200]
    return value


def parse_args():
    parser = argparse.ArgumentParser(description="Find restaurant-like lists in __NEXT_DATA__.")
    parser.add_argument("--postcode", default="ls42sw")
    parser.add_argument("--vertical", default="restaurants")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    url = build_area_url(clean_postcode(args.postcode), args.vertical, open_now=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=args.headless)
        page = browser.new_page(
            viewport={"width": 1365, "height": 900},
            user_agent=USER_AGENT,
        )
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#__NEXT_DATA__", state="attached", timeout=15000)
        next_data_text = page.locator("#__NEXT_DATA__").inner_text(timeout=5000)
        browser.close()

    data = json.loads(next_data_text)
    findings = walk(data)
    findings.sort(key=lambda item: (item["strong_first_20"], item["length"]), reverse=True)
    keyword_findings = collect_keyword_paths(data)

    for finding in findings[:20]:
        finding["first_sample"] = simplify_sample(finding["first_sample"])
        print(
            f"{finding['path']} length={finding['length']} "
            f"strong={finding['strong_first_20']} keys={finding['first_keys'][:12]}"
        )
        print(json.dumps(finding["first_sample"], ensure_ascii=False)[:800])

    if not findings:
        print("No simple restaurant-like lists found. Keyword paths:")
        for finding in keyword_findings[:80]:
            print(f"{finding['path']} kind={finding['kind']} size={finding['size']}")
            print(json.dumps(finding["sample"], ensure_ascii=False)[:500])

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(
            {"restaurant_like_lists": findings, "keyword_paths": keyword_findings},
            handle,
            ensure_ascii=False,
            indent=2,
        )
    print(
        f"Saved {len(findings)} list findings and {len(keyword_findings)} keyword paths "
        f"-> {args.output}"
    )


if __name__ == "__main__":
    main()
