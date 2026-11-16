# ADR 0009: Authenticate workflows with repository bearer tokens

- Status: Accepted
- Supersedes: [ADR 0004](0004-hmac-workflow-authentication.md)

## Context

Repository-specific authentication remains necessary, but HMAC request signing,
clock-skew validation, and authentication nonces complicate client configuration
and implementation. The operator has chosen conventional bearer authentication
over HTTPS instead, accepting the different replay and credential-exposure risks.

## Decision

Assign a distinct cryptographically random bearer token to each lowercase
repository identity. Requests send `Authorization: Bearer <repository-token>`
and `X-Demos-Repository`. The controller compares the configured token in constant
time and continues checking the payload/status repository and immutable image
namespace. Invalid credentials receive HTTP 401; cross-repository operations
receive HTTP 403. Keep repository rate limiting.

Keep the Juju secret's `credentials` JSON map, renaming the charm option to
`api-tokens-secret`, the controller environment to `API_TOKENS`, and the GitHub
secret convention to `DEMOS_API_TOKEN`. Vault AppRole authentication is unchanged.

Remove signature, timestamp, and nonce requirements. Preserve the existing
SQLite per-demo delivery ID and body hash checks for idempotent retries and
conflicting-delivery rejection. Leave the legacy nonce table intact, but unused,
to avoid a destructive database migration.

Require HTTPS with certificate validation in workflow clients. Refuse redirects
rather than risk forwarding credentials to a different host or downgrading TLS.
Do not publish HTTP error bodies that might echo Authorization headers.

## Consequences

- Workflow clients no longer need signing logic or synchronized clocks.
- GitHub and Juju token values must still match and be rotated together.
- Bearer credentials travel in each API request; HTTPS and safe logging are
  mandatory, including at the ingress and within the trusted controller network.
- Delivery idempotency is not cryptographic replay protection or permanent
  delivery history. A stolen token can authorize new requests until revoked.
- Controller and callers require a coordinated breaking cutover; no HMAC
  fallback is provided. Existing demo resources do not need to be removed.
- See the [migration guide](../bearer-token-migration.md) for rollout and rollback.
