# Responsible Data Collection

## Scope

This document records the safeguards designed into the Delivery Availability
Pipeline. It is a technical and research-governance account, not a claim that
running the software is automatically lawful or permitted by a platform.

Before each collection, the operator is responsible for reviewing the current
website terms, robots guidance, API behaviour, institutional approval, research
purpose, data-protection obligations, and expected load on the provider.

## Collection Principles

The project follows these principles:

- collect only fields needed for a defined academic research question;
- use public, unauthenticated endpoints only;
- do not bypass logins, paywalls, CAPTCHA, or other access controls;
- do not rotate proxies or identities to evade blocking;
- test parsers offline or on the smallest practical sample first;
- bound every cloud run by a known input set and explicit time window;
- minimise request frequency and avoid burst traffic;
- stop or defer traffic when the provider signals overload or refusal;
- record enough provenance to audit a run without publishing raw data; and
- keep source responses and derived datasets private.

## Traffic Controls

Cloud Tasks controls how quickly jobs are delivered to workers, but it is not
the authoritative upstream limiter. A shared state object in private Cloud
Storage serialises request starts across worker instances.

For the established postcode workflow, the research configuration used:

- queue dispatch no faster than approximately one task per second;
- bounded worker concurrency;
- at least 1300 milliseconds between upstream request starts;
- a 1000 millisecond start guard against accidental near-simultaneous starts;
- no immediate application-level retry in the API client; and
- a 3600 second shared circuit break after an HTTP 429 response.

Cloud Tasks can retry failed task delivery according to the queue policy.
Therefore the worker-level limiter and circuit break apply to every attempt.
Changing queue rate, concurrency, spacing, retry behaviour, or the target
endpoint requires a new bounded test and monitoring plan.

The menu-manifest CDN is treated as a separate upstream surface with a separate
limiter key. A result observed for one endpoint must not be assumed to establish
a safe rate for another.

## Test-To-Production Process

1. Develop parsing against saved or synthetic fixtures where possible.
2. Run a tiny local probe only when a live response is necessary.
3. Run a bounded, isolated cloud sample with separate queue, tables, and limiter
   state.
4. Inspect HTTP statuses, request-start gaps, latency, failures, and output
   schema.
5. Launch a bounded full run only after the sample is stable and authorised.
6. Monitor task depth, diagnostics, HTTP status, and collection-window spillover.
7. Stop, pause, or defer the run if the upstream service signals overload.

Automated tests and pull requests must not generate provider traffic.

## Identification And Transparency

Requests use a descriptive research User-Agent with the public repository URL.
No personal email address is embedded in the software. The repository is the
public point of contact for the method and security process.

## Data Minimisation And Privacy

The pipeline targets business listings and service availability. It does not
seek personal user accounts, orders, customer identities, payment information,
or authentication material.

Raw responses may contain fields not selected into analytical tables. They are
stored privately to support validation and controlled reprocessing. Access,
retention, and deletion must be managed in the deployment environment. Raw
responses, logs, and derived datasets must not be committed to this repository.

## Known Limitations

- A public endpoint can change or be withdrawn without notice.
- A conservative historical request rate is not a permanent guarantee against
  HTTP 403, 429, or other blocking.
- Platform opening and delivery states are observations, not contractual facts.
- Temporary offline status and missing results can have several causes.
- Postcode results may vary with time, platform ranking, service configuration,
  and undocumented backend logic.
- The software's MIT licence does not grant rights to third-party content.

## Release Checklist

Before a public software release:

- scan the current tree and Git history for credentials and private identifiers;
- confirm no data files, raw responses, logs, or browser profiles are tracked;
- use placeholders in configuration and documentation;
- verify that examples cannot accidentally target a live deployment; and
- review this protocol against the current implementation.
