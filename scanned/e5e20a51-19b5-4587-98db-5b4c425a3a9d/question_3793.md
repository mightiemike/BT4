# Q3793: Source-domain enforcement bypass in formatRequestId

## Question
Can an unprivileged attacker shape request timeout/retry ordering and cached request state so `formatRequestId` reports or enforces source domains differently from the actual outbound fetch path, leading to retrieve sensitive data/files from a running server such as database passwords and blockchain keys and breaking request/result caches must not let one user influence another request outcome?

## Target
- File/function: core/services/functions/listener.go::formatRequestId
- Entrypoint: public/onchain Functions request, bridge response, or gateway message consumed by the node
- Attacker controls: request timeout/retry ordering and cached request state
- Exploit idea: Use malicious CBOR, secret URLs, and adapter bytes to prove whether offchain request isolation, outbound fetch scope, and response ownership stay intact.
- Invariant to test: request/result caches must not let one user influence another request outcome
- Expected Immunefi impact: retrieve sensitive data/files from a running server such as database passwords and blockchain keys
- Fast validation: Use fake adapter/gateway endpoints and crafted CBOR/URL payloads; assert no internal-resource fetch, wrong-request result binding, or stale cache reuse.
