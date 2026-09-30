# Data Availability

## Software And Data Are Separate

This public repository distributes research software under the MIT License. It
does not distribute data collected from Just Eat, including:

- raw API or CDN responses;
- postcode-to-restaurant records;
- restaurant profiles;
- opening schedules;
- temporal availability observations;
- operational logs and diagnostics; or
- CSV and Parquet exports produced from private cloud tables.

The MIT License applies to the repository's source code and documentation. It
does not grant rights to third-party platform content and must not be interpreted
as a data licence.

## Reproducibility Without A Data Release

The repository documents the collection architecture, parser semantics, table
grain, provenance model, safeguards, and transformation utilities. Configuration
contains placeholders rather than deployment identities. Researchers wishing to
reuse the method must conduct their own review of current platform terms,
institutional approval, and data governance before collecting new observations.

Synthetic fixtures may be added in future for offline parser tests. They must
not reproduce identifiable records copied from private source responses.

## Citation

Software users should cite the specific archived software version through the
metadata in `CITATION.cff`. No dataset DOI or public data-access link is provided.
If the data-availability policy changes, any dataset would require separate
metadata, governance review, access terms, and citation from the software record.
