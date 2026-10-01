# Public Worklog

This worklog records reproducible software milestones. Private deployment names,
cloud identifiers, local paths, and collected data locations are intentionally
excluded.

## 2026-10-01

- Reworked the responsible-collection documentation around a four-layer traffic-control model: bounded window assignment, Cloud Tasks pacing, a cross-instance global request-start hard lock, and a shared HTTP 429 circuit breaker.
- Added exact limiter reservation semantics, retry containment, monitoring evidence, limitations, and a simplified request sequence diagram.
- Made the postcode global limiter and 429 circuit breaker enabled by default, and aligned direct task-creator queue defaults with the documented `1 task/second` and concurrency `4` configuration.
- Completed the version 1.0.0 release-candidate audit: no credentials or tracked
  research data were found, current deployment identifiers and absolute paths
  are redacted, declared dependencies reported no known vulnerabilities, and
  citation metadata passed the CFF 1.2.0 schema check.
- Added invented offline fixtures and tests for listing parsing, scheduling,
  stable identifiers, entrypoint defaults, and opening-time normalisation.
- Added portable timezone data, a 60-second interval-end scheduling guard,
  explicit task-window expiry, and sufficient queue delivery retries to span
  the one-hour 429 circuit while retaining a three-attempt provider limit.
- Added worker-side checks that reject task payloads whose run, provider,
  postcode, or observation tag does not match the recorded manifest job.

## 2026-09-30

- Prepared the repository for an open research-software release.
- Corrected the migrated MIT copyright notice to University of Leeds.
- Added `CITATION.cff`, authorship, changelog, contribution, and security files.
- Added public documentation for responsible collection, methods, data grain,
  and data availability.
- Replaced embedded project, bucket, service, queue, and account identities with
  deployment-time variables or redacted examples.
- Confirmed that no dataset, raw response, or data-access link is part of the
  public software release.

## 2026-06

- Added deterministic temporal-window scheduling for weekday afternoon,
  weekday evening, early-hours, and Saturday peak observations.
- Added a shared worker-level request-start limiter and a one-hour HTTP 429
  circuit break.
- Added millisecond-level request and limiter diagnostics.
- Added direct menu-manifest collection and normalised opening-time intervals.
- Validated the production architecture through bounded local and cloud samples
  before full research runs.

## 2026-05

- Replaced browser-based production listing collection with a direct structured
  request identified through controlled browser Network inspection.
- Added Cloud Run worker and Cloud Tasks scheduling paths.
- Added private raw-response storage and BigQuery operational provenance.
- Added raw-response backfill into static coverage and restaurant-profile
  entities.

## Ongoing Practice

- Update methods and data-model documents when behaviour or table grain changes.
- Keep live resource identities in private deployment configuration.
- Run secret and identifier scans before public releases.
- Treat provider probes and cloud writes as separately authorised operations.
