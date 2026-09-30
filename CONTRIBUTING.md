# Contributing

Contributions should preserve the separation between production code,
research probes, offline tools, and private deployment configuration.

Before opening a pull request:

1. Do not include collected data, raw responses, credentials, signed URLs,
   cookies, cloud resource identifiers, or absolute local paths.
2. Keep provider traffic disabled in automated tests. Use synthetic fixtures or
   explicitly approved, bounded manual tests.
3. Preserve request-rate controls, provenance fields, deterministic identifiers,
   and table grain unless the change explicitly revises the method.
4. Run `python -m py_compile` for changed Python modules and add focused tests
   for parsing, scheduling, or normalisation changes.
5. Update the relevant method or data-model documentation.

Live provider probes and cloud deployments must not be run from a pull request
without separate authorisation from the operator responsible for the deployment.
