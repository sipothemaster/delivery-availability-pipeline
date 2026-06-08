import json
import re
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://www.just-eat.co.uk"
JUST_EAT_API_URL = "https://uk.api.just-eat.io"
USER_AGENT = (
    "DFRE-DeliveryAvailabilityResearch/1.0 "
    "(restaurant listing checker; contact: research-contact@example.com)"
)


class JustEatAPIError(RuntimeError):
    def __init__(
        self,
        message,
        url,
        status_code=None,
        latency_ms=None,
        started_at=None,
        finished_at=None,
    ):
        super().__init__(message)
        self.url = url
        self.status_code = status_code
        self.latency_ms = latency_ms
        self.started_at = started_at
        self.finished_at = finished_at


def utc_now_precise():
    return datetime.now(timezone.utc)


def clean_postcode(postcode):
    return re.sub(r"\s+", "", str(postcode)).lower()


def clean_text(value):
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value)).strip()


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


def build_area_path(postcode, area_slug=None):
    return f"{postcode}-{area_slug}" if area_slug else postcode


def build_listing_api_url(postcode, area_slug=None):
    area_path = build_area_path(postcode, area_slug=area_slug)
    query = urlencode({"serviceType": "delivery"})
    return f"{JUST_EAT_API_URL}/discovery/uk/restaurants/enriched/byslug/{area_path}?{query}"


def restaurant_url_from_api(restaurant):
    unique_name = restaurant.get("uniqueName")
    restaurant_id = restaurant.get("id")
    if unique_name and restaurant_id:
        return f"{BASE_URL}/restaurants-{unique_name}/menu"
    return None


def fetch_listing_api(postcode, area_slug=None, include_metadata=False):
    url = build_listing_api_url(postcode, area_slug=area_slug)
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Referer": f"{BASE_URL}/area/{build_area_path(postcode, area_slug=area_slug)}",
        },
    )
    started_at = utc_now_precise()
    started_perf = time.perf_counter()
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            finished_at = utc_now_precise()
            latency_ms = int(round((time.perf_counter() - started_perf) * 1000))
            metadata = {
                "api_request_started_at": started_at,
                "api_request_finished_at": finished_at,
                "api_latency_ms": latency_ms,
                "http_status": getattr(response, "status", 200),
            }
            data = json.loads(body)
            if include_metadata:
                return data, url, metadata
            return data, url
    except HTTPError as exc:
        finished_at = utc_now_precise()
        latency_ms = int(round((time.perf_counter() - started_perf) * 1000))
        raise JustEatAPIError(
            f"Just Eat API returned HTTP {exc.code}: {url}",
            url=url,
            status_code=exc.code,
            latency_ms=latency_ms,
            started_at=started_at,
            finished_at=finished_at,
        ) from exc
    except URLError as exc:
        finished_at = utc_now_precise()
        latency_ms = int(round((time.perf_counter() - started_perf) * 1000))
        raise JustEatAPIError(
            f"Could not reach Just Eat API: {exc}",
            url=url,
            latency_ms=latency_ms,
            started_at=started_at,
            finished_at=finished_at,
        ) from exc


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
