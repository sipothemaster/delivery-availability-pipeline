# Changelog

All notable changes to released versions are documented here.

## 1.0.0 - 2026-10-01

- Prepared the cloud pipeline for public research-software release.
- Added citation, authorship, responsible-collection, methods, data-model, and
  data-availability documentation.
- Replaced deployment-specific cloud identifiers with environment variables and
  redacted examples.
- Clarified that collected data and raw responses are not distributed with the
  software.
- Added synthetic offline tests for listing parsing, temporal scheduling,
  deterministic identifiers, task-creator defaults, and opening-time
  normalisation.
- Added portable IANA timezone data, a one-minute scheduling end guard, hard
  observation-window expiry, and a queue retry budget that can span the shared
  one-hour 429 circuit without increasing provider attempts.
- Added worker-side manifest identity validation before any provider request.
