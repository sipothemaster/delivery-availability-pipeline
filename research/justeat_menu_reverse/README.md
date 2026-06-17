# Just Eat Menu Reverse Engineering Notes

This folder preserves the Just Eat restaurant menu reverse-engineering work.

It is not part of the current postcode availability production pipeline. It is
kept as research for a future menu pipeline and downstream "affordably healthy"
analysis.

## Main Script

```text
reverse_justeat_menu.py
```

The script reads saved restaurant page HTML files, extracts menu metadata from
`__NEXT_DATA__`, downloads menu CDN JSON files when available, and writes
normalized CSV outputs.

Additional probe:

```text
probe_justeat_menu_cdn.py
```

This script verifies that restaurant menu CDN JSON can be requested directly
from the restaurant slug/`restaurant_unique_name`, without first downloading
the restaurant HTML page.

## Data Source Relationship

Observed structure:

```text
Restaurant HTML page
  -> __NEXT_DATA__
    -> props.appProps.preloadedState.menu.restaurant.cdn.restaurant
      -> menu/category/item id skeleton
      -> itemsUrl
      -> itemDetailsUrl
      -> truncatedUrl

CDN items JSON
  -> Items[]
    -> item names, descriptions, variations, prices, images, labels, kcal
    -> modifier group ids
    -> deal group ids

CDN itemDetails JSON
  -> ModifierGroups[]
  -> ModifierSets[]
  -> DealGroups[]
```

CDN base observed during research:

```text
https://menu-globalmenucdn.je-apis.com
```

Later reverse-engineering found that the CDN paths can be constructed directly
from the restaurant slug:

```text
https://menu-globalmenucdn.je-apis.com/{slug}_uk_manifest.json
https://menu-globalmenucdn.je-apis.com/v2_2/{slug}_uk_manifest.json
https://menu-globalmenucdn.je-apis.com/{slug}_uk_items.json
https://menu-globalmenucdn.je-apis.com/{slug}_uk_itemDetails.json
```

The manifest contains restaurant metadata, opening times, menu/category
schedules, and the `ItemsUrl` / `ItemDetailsUrl` values. This means the future
menu pipeline can usually use `restaurant_profile.restaurant_unique_name` to
fetch menu data directly from the CDN and avoid the heavier restaurant HTML
request.

Dynamic menu API observed:

```text
https://uk.api.just-eat.io/restaurant/uk/{restaurant_id}/menu/dynamic?orderTime=...
```

The dynamic API contained live state/rating/offline/fee data, but not the full
menu. Complete menu extraction normally needed:

```text
1 restaurant HTML request
+ 1 items.json CDN request
+ 1 itemDetails.json CDN request
```

## Outputs

The research run generated CSVs such as:

```text
summary.csv
menu_items.csv
modifier_options.csv
deal_options.csv
```

These outputs are intentionally not committed.
