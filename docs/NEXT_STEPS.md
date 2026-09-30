# Roadmap

## Before Version 1.0.0

1. Add offline tests for listing parsing, temporal scheduling, opening-time
   normalisation, and deterministic identifiers.
2. Add synthetic fixtures that contain no copied provider records.
3. Run dependency, secret, absolute-path, and deployment-identifier scans.
4. Review the responsible-collection account against the released code.
5. Confirm the University of Leeds copyright and release approval record.
6. Connect the GitHub repository to Zenodo and publish a tagged release.
7. Add the resulting version DOI to `CITATION.cff` and the README.

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
