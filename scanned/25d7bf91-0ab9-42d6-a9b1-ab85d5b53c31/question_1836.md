# Q1836: Adapter response parsing differential in FindBridge

## Question
Can an unprivileged attacker supply CBOR payloads, encrypted-secrets URLs, and request IDs so `FindBridge` accepts malformed, truncated, or ambiguous adapter output that downstream consumers treat as valid, leading to authentication bypass into bridge or external-initiator privileged behavior and violating adapter output and caches must not let one attacker-controlled request influence another job or principal?

## Target
- File/function: core/bridges/cache.go::FindBridge
- Entrypoint: POST /v2/bridge_types, POST /v2/external_initiators, or public/offchain adapter input consumed by the node
- Attacker controls: CBOR payloads, encrypted-secrets URLs, and request IDs
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: adapter output and caches must not let one attacker-controlled request influence another job or principal
- Expected Immunefi impact: authentication bypass into bridge or external-initiator privileged behavior
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
