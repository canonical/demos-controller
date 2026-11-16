# Canonical PR demos controller

This repository contains the controller-based replacement for the legacy
Terraform, per-PR Juju application, Docker, Jenkins, and direct Kubernetes demo
flows.

The repository root contains the FastAPI deployment API, Vault client,
desired-state store, Kubernetes reconciler, reverse proxy, tests, and workload
rock. The remaining components are:

- `charm/`: the trusted Kubernetes charm and `haproxy-route` integration;
- `.github/workflows/`: reusable PR build/deploy and cleanup workflows;
- `tests/fixtures/demo-app/`: a minimal non-root demo rock;
- `tests/integration/fake_vault.py`: an isolated Vault-compatible integration
  fixture.

Project configuration and application secrets are resolved from externally
managed Vault records. GitHub Actions sends only repository identity, PR and
commit metadata, and an immutable repository-scoped image digest.

## Controller API

The workload serves the authenticated deployment API on port 8080, persists
non-secret desired state in SQLite, resolves project configuration through Vault
AppRole and KV v2, reconciles resources with Lightkube, and proxies active demo
hosts.

Every deploy, destroy, and status request provides:

```http
Authorization: Bearer <repository-token>
X-Demos-Repository: canonical/example-repository
```

Configure `API_TOKENS` as a JSON map of lowercase `owner/repository` identities
to distinct random bearer tokens. A token authorizes only its matching
repository, including the repository in a deployment image reference. Missing
or invalid credentials return HTTP 401 with a Bearer authentication challenge;
cross-repository operations return HTTP 403. Health probes remain unauthenticated.

`POST /api/v1/deploy` and `/api/v1/destroy` retain the payload's `delivery_id`.
An identical retry of the currently stored delivery is idempotent; reusing that
ID with a different body for the same demo returns HTTP 409. This is operation
idempotency, not protection against a stolen token or indefinite replay of old
deliveries. Status is available at
`GET /api/v1/demos/{owner}/{repository}/{pr}`.

Expose the API over HTTPS only and never log Authorization headers. The supplied
client requires HTTPS with normal certificate validation, refuses redirects,
and omits HTTP error bodies that could echo credentials. Rotate compromised
tokens in Juju and GitHub together. Vault AppRole authentication is unchanged.

HMAC signatures, timestamps, and nonce headers are no longer accepted as
authentication. The old SQLite nonce table is retained for database compatibility
but is no longer used.

Vault records at `<mount>/data/<base>/v1/<owner>/<repository>` use schema
version 1 and distinguish `environment` from `secrets`. Secret values are never
written to SQLite.

Images must use immutable digests beneath the authenticated repository path.
The registry defaults to `ghcr.io` and can be overridden with `IMAGE_REGISTRY`
for isolated development registries.

See [`charm/README.md`](charm/README.md) for operator details and the
[bearer-token migration guide](docs/bearer-token-migration.md) before upgrading.

## Documentation

- [`AGENTS.md`](AGENTS.md): repository rules, invariants, validation, and agent
  handoff guidance.
- [`docs/architecture.md`](docs/architecture.md): end-to-end architecture and
  request lifecycle.
- [`docs/configuration.md`](docs/configuration.md): Juju, Vault, GitHub, and
  project configuration schemas.
- [`docs/operations.md`](docs/operations.md): deployment, verification,
  recovery, and troubleshooting runbook.
- [`docs/history.md`](docs/history.md): implementation history and production
  lessons.
- [`docs/adr/`](docs/adr/): accepted architecture decision records.
