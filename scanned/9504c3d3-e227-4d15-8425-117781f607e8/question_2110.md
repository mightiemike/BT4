# Q2110: Boundary preservation edge case in ValidateExternalInitiator #4

## Question
Can an unprivileged attacker use CBOR payloads, encrypted-secrets URLs, and request IDs at `bridge or external-initiator REST path` so `ValidateExternalInitiator` reaches a concrete path to retrieve sensitive data/files from a running server such as database passwords and blockchain keys by breaking the invariant that outbound fetches must stay confined away from local files and sensitive internal endpoints, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/external_initiators_controller.go::ValidateExternalInitiator
- Entrypoint: bridge or external-initiator REST path
- Attacker controls: CBOR payloads, encrypted-secrets URLs, and request IDs
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: outbound fetches must stay confined away from local files and sensitive internal endpoints
- Expected Immunefi impact: retrieve sensitive data/files from a running server such as database passwords and blockchain keys
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
