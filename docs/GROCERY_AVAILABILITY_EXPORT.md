# Grocery Availability Export

This document defines the postcode-level Just Eat grocery availability CSV for
stakeholder use. The CSV is a grouped output, not a restaurant-level extract.
It is generated from existing BigQuery tables and never creates scrape tasks or
calls Just Eat.

## Output Unit

Each row represents one:

```text
postcode x LSOA/Data Zone x source tag
```

The geography inputs use one representative postcode per LSOA in England and
Wales, and one representative postcode per Data Zone in Scotland. A boundary
postcode can legitimately occur for more than one geography code; each such
postcode-to-geography assignment is retained.

The output columns are:

- `postcode`: lower-case postcode without spaces, matching the pipeline input.
- `geo_code`: `LSOA21CD` in England/Wales or `DZCode` in Scotland.
- `geo_type`: `lsoa` or `dz`.
- `source_dataset`: `static_map` or `temporal_snapshot`.
- `source_tag`: `full_coverage` for the static map, or a temporal window tag.
- `window_start_local`, `window_end_local`: local scheduled time bounds for
  temporal observations; blank for the static map.
- `big_brand_grocery_count`: distinct deliverable restaurants matching the six
  large grocery brands below.
- `general_grocery_count`: distinct deliverable restaurants matching the
  broader recognised food-grocery retailer list below.
- `justeat_groceries_cuisine_tag_count`: distinct deliverable restaurants with
  the strict Just Eat `Groceries`/`Grocery` cuisine tag definition below.
- `has_coop`, `has_morrisons`, `has_sainsburys`, `has_asda`, `has_iceland`,
  `has_waitrose`: `yes` or `no`, based on at least one deliverable restaurant
  for that brand in the same row.

The three count columns are alternative classifications and must not be added
together.

## Source Data And Deliverability

`static_map` is derived from
`postcode_restaurant_delivery_map`, using `is_delivery = true`. It represents
the broad delivery-coverage map created from the full Just Eat API response.
The exported static-map tag is `full_coverage`. Its current underlying source
label is `weekday_full_20260520`; this provenance stays in the production table
rather than the stakeholder CSV.

`temporal_snapshot` is derived from
`restaurant_snapshots_temporal_snapshot_202606`. Those rows already passed the
production open-delivery parser:

```text
isDelivery = true
isOpenNowForDelivery = true
isTemporarilyOffline = false
```

Each postcode was assigned exactly once to each temporal tag. The multiple
calendar intervals in the configuration were dispatch shards used to spread one
national run across the available days; they are not repeat measurements. A
postcode with no qualifying restaurant still appears with zero counts and `no`
brand flags.

## Temporal Windows

These are the windows actually scheduled in
`configs/temporal_snapshot_windows_202606.json`:

| Tag | Local time | Dispatch dates |
| --- | --- | --- |
| `weekday_afternoon` | 14:00-18:00 | 17, 18, 24, 25 June 2026 |
| `weekday_evening` | 18:30-22:30 | 17, 18, 24, 25 June 2026 |
| `weekday_early_hours` | 00:00-04:00 | 18, 19, 25, 26 June 2026 |
| `saturday_peak` | 14:00-22:00 | 20, 27 June 2026 |

The dates in this table are task-dispatch shards, not separate output rows. The
CSV has one row per postcode and tag. It excludes the small `smoke_temporal`
validation run.

## Grocery Classifications

### Big Brand

`big_brand_grocery_count` and the six `has_*` columns use a conservative
restaurant-name match for:

- Co-op
- Morrisons
- Sainsbury's
- Asda, excluding Asda Cafe
- Iceland
- Waitrose

These are the primary comparison indicators. `Tesco` is not included because
the stored Just Eat profile data did not provide a usable national Tesco set.

### General Grocery

`general_grocery_count` includes the six big brands plus recognised food and
convenience retail chains:

- Londis
- One Stop
- Spar
- Premier
- Nisa
- Budgens
- Costcutter
- Best-one
- Gopuff
- Family Shopper
- Lifestyle Express
- Go Local
- Shop N Drive

It deliberately excludes unclassified independent stores and specialist or
non-food retailers. It also excludes pharmacy, beauty, vape, pet, and
alcohol-only chains such as Boots, Superdrug, VPZ, Majestic Wine, and Bargain
Booze. This gives a wider but still conservative food-grocery measure.

### Official Just Eat Cuisine Tag

`justeat_groceries_cuisine_tag_count` retains a platform-defined comparison
measure. It requires a `Groceries` or `Grocery` cuisine label while excluding
restaurants also labelled `Convenience` or `Supermarket`.

This is a raw Just Eat classification, not a cleaned food-grocery measure, so
it can still include non-food or alcohol-led businesses. It also does not
equal the website's `?vertical=groceries` page result: that page uses a
separate Next.js server-rendered list that was not stored in the historical
API raw JSON.
