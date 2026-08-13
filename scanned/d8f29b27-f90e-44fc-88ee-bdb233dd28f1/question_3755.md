# Q3755: Boundary preservation edge case in RunComputation #6

## Question
Can an unprivileged attacker use source-code domain lists and external-fetch metadata at `public/onchain Functions request, bridge response, or gateway message consumed by the node` so `RunComputation` reaches a concrete path to execute arbitrary system commands through unsafe adapter or request execution by breaking the invariant that request size, source domains, and secrets fetching must stay within enforced limits, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/services/functions/external_adapter_client.go::RunComputation
- Entrypoint: public/onchain Functions request, bridge response, or gateway message consumed by the node
- Attacker controls: source-code domain lists and external-fetch metadata
- Exploit idea: Use malicious CBOR, secret URLs, and adapter bytes to prove whether offchain request isolation, outbound fetch scope, and response ownership stay intact.
- Invariant to test: request size, source domains, and secrets fetching must stay within enforced limits
- Expected Immunefi impact: execute arbitrary system commands through unsafe adapter or request execution
- Fast validation: Use fake adapter/gateway endpoints and crafted CBOR/URL payloads; assert no internal-resource fetch, wrong-request result binding, or stale cache reuse.
