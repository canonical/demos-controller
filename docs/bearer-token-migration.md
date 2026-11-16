# Migrate workflow authentication to bearer tokens

The controller now authenticates deploy, destroy, and status requests with
repository-specific bearer tokens instead of HMAC signatures. This is a
**breaking, coordinated controller/client change**. There is no HMAC fallback.
Vault AppRole authentication and repository configuration records are unchanged.

## Secret and configuration names

| Previous | Replacement |
|---|---|
| Charm `hmac-credentials-secret` | Charm `api-tokens-secret` |
| Workload `HMAC_CREDENTIALS` | Workload `API_TOKENS` |
| GitHub Actions `DEMOS_HMAC_KEY` | GitHub Actions `DEMOS_API_TOKEN` |
| Reusable workflow secret `demos-hmac-key` | Reusable workflow secret `demos-api-token` |
| Client `--key-env` | Client `--token-env` |
| Charm `signature-max-age` / workload `SIGNATURE_MAX_AGE` | Removed |

The Juju user secret retains its `credentials` field. That field is a JSON
string mapping lowercase `owner/repository` names to individual tokens. Do not
supply the entire map as a repository's GitHub secret: supply only its own token.

Generate a distinct token from at least 32 cryptographically random bytes for
each repository, encoded as hex or URL-safe base64. Use your approved secret
manager or protected temporary files; never print tokens, put them in command
arguments or shared shell history, commit them, or paste them into PRs.

Existing sufficiently random HMAC key values can be reused as token values if
approved. The secret configuration names and HTTP protocol still change.

## Coordinated cutover

1. Inventory callers, including reusable workflows in `canonical/webteam-devops`.
   Prepare matching bearer tokens in Juju and each repository's GitHub Actions
   `DEMOS_API_TOKEN` secret. Do not modify Vault records for this change.
2. Pause demo workflow triggers during the cutover if necessary. Preserve the
   old charm/image and workflow revisions for a coordinated rollback.
3. Refresh the controller charm and image together to the bearer-token revision.
   Configure and grant its Juju secret, replacing the placeholder with its URI:

   ```bash
   juju grant-secret <token-secret-uri> demos-controller
   juju config demos-controller api-tokens-secret=<token-secret-uri>
   ```

4. Pin callers to audited bearer-token workflow/client commits. Change reusable
   workflow callers to:

   ```yaml
   secrets:
     demos-api-token: ${{ secrets.DEMOS_API_TOKEN }}
   ```

   Callers using this repository's workflows must also update `workflow-client-ref`
   to the bearer-token client revision; an old pinned client is incompatible.
5. Verify health, deployment, authenticated status polling, identical retries,
   and cleanup. Resume workflow triggers and retire the old secret/configuration
   after successful verification. Do not delete existing demos during cutover.

If rollback is needed, roll back both controller and callers together, restoring
matching HMAC configuration. No destructive SQLite migration is performed; the
old nonce table remains, unused by bearer authentication.

## Security and retry behavior

Requests send `Authorization: Bearer <repository-token>` and
`X-Demos-Repository: <owner/repository>`. Tokens authorize only their matching
repository. Signature, timestamp, and nonce headers are no longer required and
do not authenticate a request.

Expose the API through HTTPS only; protect the internal controller/ingress
network too. Never log Authorization headers. The supplied client validates TLS,
rejects non-HTTPS URLs and redirects, and omits HTTP error bodies that might echo
credentials. Do not expose the token to untrusted pull-request code.

Payload `delivery_id` and request-body hashes retain the existing per-demo
idempotency behavior: an identical retry of the currently stored delivery is
safe, and different content with that ID returns HTTP 409. This is not a permanent
delivery history or cryptographic replay protection. A stolen bearer token can
create new requests until revoked. Rotate tokens in Juju and GitHub together.
