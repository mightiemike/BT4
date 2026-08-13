# Q3845: CBOR or flag parsing differential in pruneRequests

## Question
Can an unprivileged attacker use CBOR request bytes, flags, and subscription metadata so `pruneRequests` computes request limits or semantics from one interpretation while downstream execution uses another, leading to execute arbitrary system commands through unsafe adapter or request execution and violating offchain request IDs and subscription ownership must remain consistently bound through retries and timeouts?

## Target
- File/function: core/services/functions/listener.go::pruneRequests
- Entrypoint: public/onchain Functions request, bridge response, or gateway message consumed by the node
- Attacker controls: CBOR request bytes, flags, and subscription metadata
- Exploit idea: Use malicious CBOR, secret URLs, and adapter bytes to prove whether offchain request isolation, outbound fetch scope, and response ownership stay intact.
- Invariant to test: offchain request IDs and subscription ownership must remain consistently bound through retries and timeouts
- Expected Immunefi impact: execute arbitrary system commands through unsafe adapter or request execution
- Fast validation: Use fake adapter/gateway endpoints and crafted CBOR/URL payloads; assert no internal-resource fetch, wrong-request result binding, or stale cache reuse.
