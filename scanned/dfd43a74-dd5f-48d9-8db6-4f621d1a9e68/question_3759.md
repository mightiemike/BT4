# Q3759: Secrets URL fetch escape in RunComputation

## Question
Can an unprivileged attacker supply encryptedSecretsUrls bytes, requestId, and jobName so `RunComputation` follows redirects, alternate schemes, or encoded targets into sensitive internal resources, leading to retrieve sensitive data/files from a running server such as database passwords and blockchain keys and violating request size, source domains, and secrets fetching must stay within enforced limits?

## Target
- File/function: core/services/functions/external_adapter_client.go::RunComputation
- Entrypoint: public/onchain Functions request, bridge response, or gateway message consumed by the node
- Attacker controls: encryptedSecretsUrls bytes, requestId, and jobName
- Exploit idea: Use malicious CBOR, secret URLs, and adapter bytes to prove whether offchain request isolation, outbound fetch scope, and response ownership stay intact.
- Invariant to test: request size, source domains, and secrets fetching must stay within enforced limits
- Expected Immunefi impact: retrieve sensitive data/files from a running server such as database passwords and blockchain keys
- Fast validation: Use fake adapter/gateway endpoints and crafted CBOR/URL payloads; assert no internal-resource fetch, wrong-request result binding, or stale cache reuse.
