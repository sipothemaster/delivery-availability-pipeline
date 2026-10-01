# Releasing And Citation

## Release Preparation

1. Confirm that `main` contains only public software and documentation.
2. Run syntax checks and offline tests.
3. Scan the working tree and Git history for credentials, private identifiers,
   absolute paths, generated data, and large binaries.
4. Confirm `AUTHORS.md`, `CITATION.cff`, `CHANGELOG.md`, and `LICENSE` agree on
   authorship, version, and copyright.
5. Review `ETHICAL_DATA_COLLECTION.md` against the implementation.
6. Confirm that no collected data or data-access link is included.

## GitHub And Zenodo

1. Sign in to Zenodo using the account associated with the GitHub repository.
2. Enable the repository in Zenodo's GitHub integration before creating the
   release.
3. Create and push an annotated version tag, beginning with `v1.0.0`.
4. Publish the corresponding GitHub Release.
5. Verify that Zenodo archives the release and creates a version DOI.
6. Record both the version DOI and the concept DOI used to identify all versions.
7. Add the version DOI to `CITATION.cff` and a DOI badge to `README.md` in the
   next documentation update.

Publications should cite the DOI for the exact software version used. The
concept DOI is useful when referring to the software generally across versions.

## Metadata

The initial release metadata are:

- title: Delivery Availability Pipeline;
- author: Chenrui Xiao;
- affiliation: Leeds Institute for Data Analytics, University of Leeds;
- copyright holder: University of Leeds;
- licence: MIT; and
- repository: `https://github.com/sipothemaster/delivery-availability-pipeline`.

No ORCID, personal email, grant, or dataset identifier is included. Zenodo must
archive the software release only; collected data must not be attached.

## Post-Release Verification

- Open the Zenodo record while signed out and confirm only intended files are
  visible.
- Download the archived source and repeat the identifier and secret scans.
- Verify that GitHub's **Cite this repository** output matches `CITATION.cff`.
- Verify that the release date in `CHANGELOG.md` and `CITATION.cff` matches the
  published release.
- Do not reuse a version number after publication; release fixes under a new
  semantic version.
