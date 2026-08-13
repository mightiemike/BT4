# Q2107: Boundary preservation edge case in ValidateExternalInitiator #1

## Question
Can an unprivileged attacker use bridge URL, bridge name, payment floor, and adapter metadata at `bridge or external-initiator REST path` so `ValidateExternalInitiator` reaches a concrete path to misreporting of prices and/or data by breaking the invariant that outbound fetches must stay confined away from local files and sensitive internal endpoints, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/external_initiators_controller.go::ValidateExternalInitiator
- Entrypoint: bridge or external-initiator REST path
- Attacker controls: bridge URL, bridge name, payment floor, and adapter metadata
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: outbound fetches must stay confined away from local files and sensitive internal endpoints
- Expected Immunefi impact: misreporting of prices and/or data
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
