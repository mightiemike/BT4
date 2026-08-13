# Q2113: Bridge cache poisoning in ValidateExternalInitiator

## Question
Can an unprivileged attacker use external-initiator name, URL, and generated auth-token pairing so `ValidateExternalInitiator` stores or retrieves adapter output under a cache key later consumed by a different job or principal, causing misreporting of prices and/or data and breaking outbound fetches must stay confined away from local files and sensitive internal endpoints?

## Target
- File/function: core/web/external_initiators_controller.go::ValidateExternalInitiator
- Entrypoint: bridge or external-initiator REST path
- Attacker controls: external-initiator name, URL, and generated auth-token pairing
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: outbound fetches must stay confined away from local files and sensitive internal endpoints
- Expected Immunefi impact: misreporting of prices and/or data
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
