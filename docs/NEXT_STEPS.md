# Roadmap

## Before Version 1.0.0

Release-candidate preparation completed on 2026-10-01: offline tests and
synthetic fixtures were added; dependencies, credentials, paths, identifiers,
tracked data, and documentation-to-code consistency were audited; and the
`CITATION.cff` schema was validated.

Remaining publication steps are:

1. Confirm the University of Leeds release approval record.
2. Connect the GitHub repository to Zenodo and publish the `v1.0.0` tag and
   GitHub Release.
3. Add the resulting version DOI to `CITATION.cff` and the README.

## Engineering Improvements

- Replace mutable manifest status with an explicitly tested state transition or
  document append-only events as the sole completion authority.
- Add deployment templates that create least-privilege identities without
  embedding real resource names.
- Add budget alerts and retention-policy guidance to deployment documentation.
- Add structured validation reports for window spillover, HTTP outcomes,
  request-start gaps, and duplicate analytical keys.
- Package the offline parser so it can be demonstrated entirely from synthetic
  fixtures.
- Add a cross-platform dependency lock or constraints file for exact environment
  reconstruction in addition to the tested direct requirements.

## Research Extensions

- Evaluate whether opening schedules can support derived availability measures
  without replacing observed open-now snapshots.
- Develop menu-item analysis only where the research question requires it.
- Keep other delivery platforms in separate provider modules and validate each
  endpoint independently.
- Treat grocery classification lenses as overlapping measures rather than
  mutually exclusive categories.

## Data Governance

No collected data are publicly released. Any future data-sharing proposal must
undergo a separate rights, ethics, governance, documentation, and licensing
review and must receive its own citation record.
