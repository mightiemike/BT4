# Q2076: Adapter response parsing differential in ValidateBridgeTypeNotExist

## Question
Can an unprivileged attacker supply CBOR payloads, encrypted-secrets URLs, and request IDs so `ValidateBridgeTypeNotExist` accepts malformed, truncated, or ambiguous adapter output that downstream consumers treat as valid, leading to authentication bypass into bridge or external-initiator privileged behavior and violating adapter output and caches must not let one attacker-controlled request influence another job or principal?

## Target
- File/function: core/web/bridge_types_controller.go::ValidateBridgeTypeNotExist
- Entrypoint: bridge or external-initiator REST path
- Attacker controls: CBOR payloads, encrypted-secrets URLs, and request IDs
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: adapter output and caches must not let one attacker-controlled request influence another job or principal
- Expected Immunefi impact: authentication bypass into bridge or external-initiator privileged behavior
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
