# Q3787: Boundary preservation edge case in formatRequestId #5

## Question
Can an unprivileged attacker use request timeout/retry ordering and cached request state at `public/onchain Functions request, bridge response, or gateway message consumed by the node` so `formatRequestId` reaches a concrete path to misreporting of prices and/or data by breaking the invariant that offchain request IDs and subscription ownership must remain consistently bound through retries and timeouts, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/services/functions/listener.go::formatRequestId
- Entrypoint: public/onchain Functions request, bridge response, or gateway message consumed by the node
- Attacker controls: request timeout/retry ordering and cached request state
- Exploit idea: Use malicious CBOR, secret URLs, and adapter bytes to prove whether offchain request isolation, outbound fetch scope, and response ownership stay intact.
- Invariant to test: offchain request IDs and subscription ownership must remain consistently bound through retries and timeouts
- Expected Immunefi impact: misreporting of prices and/or data
- Fast validation: Use fake adapter/gateway endpoints and crafted CBOR/URL payloads; assert no internal-resource fetch, wrong-request result binding, or stale cache reuse.
