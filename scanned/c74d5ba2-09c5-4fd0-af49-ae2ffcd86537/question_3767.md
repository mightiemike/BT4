# Q3767: Bridge-response poisoning in request

## Question
Can an unprivileged attacker exploit gatewayID, JSON-RPC request body, and connector message IDs so `request` persists, truncates, or retries adapter output in a way that later yields attacker-chosen data to another request, causing theft of protocol revenue through replayed or confused request handling and violating request size, source domains, and secrets fetching must stay within enforced limits?

## Target
- File/function: core/services/functions/external_adapter_client.go::request
- Entrypoint: public/onchain Functions request, bridge response, or gateway message consumed by the node
- Attacker controls: gatewayID, JSON-RPC request body, and connector message IDs
- Exploit idea: Use malicious CBOR, secret URLs, and adapter bytes to prove whether offchain request isolation, outbound fetch scope, and response ownership stay intact.
- Invariant to test: request size, source domains, and secrets fetching must stay within enforced limits
- Expected Immunefi impact: theft of protocol revenue through replayed or confused request handling
- Fast validation: Use fake adapter/gateway endpoints and crafted CBOR/URL payloads; assert no internal-resource fetch, wrong-request result binding, or stale cache reuse.
