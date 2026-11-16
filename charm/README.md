# Canonical demos controller charm

Deploy this Kubernetes charm with `--trust`, attach persistent `state` storage,
and relate `haproxy-route` to the existing HAProxy offer. The route publishes
the exact `api-hostname` plus the wildcard beneath `hostname-suffix`. Configure
`vault-approle-secret` with a Juju user secret containing `role-id` and
`secret-id`. Configure `api-tokens-secret` with a `credentials` key whose
value is a JSON map from lowercase `owner/repository` identities to distinct
random bearer tokens. Store each repository's matching token in its GitHub
Actions secret `DEMOS_API_TOKEN`. Use at least 32 cryptographically random bytes
per token, encoded as hex or URL-safe base64. Never print or log tokens.

See the [bearer-token migration guide](../docs/bearer-token-migration.md) before
upgrading an existing deployment. HMAC-only workflow clients are incompatible
with the new controller.

By default, the charm publishes `demos-controller.canonical.com` and
`*.demos.canonical.com` to its port 8080.
Because HAProxy runs outside the Kubernetes model, the charm creates an owned
NodePort Service selecting the Juju application pods and publishes Kubernetes
node `InternalIP` addresses plus the allocated NodePort through JSON-encoded
`haproxy-route` v2 application data. Cluster-wide node discovery and Service
management require deploying the charm with `--trust`.

The workload stores only non-secret desired state in `/data/controller.db`;
application secret values are read from Vault and projected directly into
controller-owned Kubernetes Secrets.

`image-registry` defaults to `ghcr.io`. Override it only for a trusted private
or local registry; repository matching and immutable digest validation remain
mandatory.

The Charmhub OCI resource is named `app-image` to match the shared
`canonical/webteam-devops` pack-and-release workflow.
