# Security Policy

## Supported Version

Security fixes are applied to the latest released version.

## Reporting

Do not place credentials, private data, signed URLs, cookies, internal cloud
identifiers, or exploitable deployment details in a public issue. Use GitHub's
private vulnerability reporting feature for this repository when available.

## Repository Rules

- Cloud credentials must be supplied through Application Default Credentials or
  a managed secret/configuration mechanism.
- Real project ids, bucket names, service URLs, service-account addresses, and
  local absolute paths must not be committed.
- Collected data, raw responses, logs, and generated exports must remain outside
  Git.
- Cloud Run services should require authentication and service accounts should
  follow least privilege.
- A secret scan and identifier scan are required before every public release.

The repository URL is the public contact point because no individual email
address is published for this project.
