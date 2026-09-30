# Methods

## Discovery Process

The project began with browser automation because the structure of the Just Eat
listing page and its data sources were not yet known. Playwright loaded a public
area page in a normal browser context, while the research scripts inspected:

- rendered DOM content;
- embedded Next.js `__NEXT_DATA__` state;
- JavaScript bundle references; and
- browser Network responses.

The purpose was to identify the structured response already used by the public
page, not to automate account actions or bypass access controls. Controlled
endpoint probes were then used to confirm the request shape and response fields.

Once the structured listing endpoint was validated, the production pipeline
stopped depending on browser rendering. Direct JSON requests reduced page load,
bandwidth, browser overhead, and parsing ambiguity. Historical discovery scripts
remain under `research/justeat_api_reverse/` so the transition is auditable.

Menu research followed the same staged approach. Page metadata and Network
responses revealed a public CDN manifest keyed by the restaurant's stable slug.
The production opening-time workflow requests that manifest directly and uses a
documented fallback path. It does not fetch item details or modifier groups when
opening intervals are the only research requirement.

## Cloud Workflow

The task-creator job reads a postcode input, constructs deterministic job ids,
assigns jobs to explicit local-time intervals, records a manifest, and creates
scheduled Cloud Tasks. Parallel task creation affects only insertion into Cloud
Tasks; it does not determine the upstream request rate.

Each authenticated Cloud Task invokes a Flask endpoint on Cloud Run. Before an
upstream request, the worker:

1. validates the task payload and observation window;
2. checks the shared HTTP 429 circuit state;
3. acquires a global request-start reservation in private Cloud Storage;
4. records millisecond-level limiter and request timing;
5. performs one structured request;
6. stores the complete response privately in compressed form; and
7. appends operational and parsed rows to private BigQuery tables.

The global reservation is authoritative across worker instances. Queue rate and
concurrency are additional controls, not replacements for the shared limiter.

## Window Assignment

When a tag has several eligible intervals, postcodes are deterministically
distributed across those intervals. The intervals are capacity shards for one
observation tag; they do not mean every postcode is collected once per date.
This prevents accidental multiplication of an intended single observation.

The worker and scheduler must leave capacity margin so tasks finish inside the
intended interval. Runs use distinct identifiers, table suffixes, queue names,
and limiter state when isolation is necessary.

## Parsing

The listing response is retained privately before filtering. The temporal
snapshot parser selects records reported as delivery-enabled, currently open for
delivery, and not temporarily offline. A separate backfill utility reads private
raw responses to construct the broader postcode-to-restaurant coverage map and
deduplicated restaurant profile.

Menu opening intervals are normalised to one row per restaurant, service,
weekday, and interval. Cross-midnight intervals are explicitly marked.

## Validation

Validation proceeds from offline parsing to a tiny local probe, then an isolated
cloud sample, and finally a bounded full run. Checks include:

- deterministic job and snapshot identifiers;
- request-start gaps and queue throughput;
- HTTP status and latency distributions;
- 403, 429, and 5xx outcomes;
- task success, failure, and deferral counts;
- collection-window spillover;
- parsed row counts and distinct keys;
- raw-to-parsed field checks; and
- expected joins between profiles, coverage, schedules, and snapshots.

The detailed responsible-collection controls are documented separately in
`ETHICAL_DATA_COLLECTION.md`.
