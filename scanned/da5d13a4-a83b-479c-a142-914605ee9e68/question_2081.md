# Q2081: Boundary preservation edge case in ValidateBridgeTypeNotExist #5

## Question
Can an unprivileged attacker use job-owned bridge names, dot IDs, and cached response slots at `bridge or external-initiator REST path` so `ValidateBridgeTypeNotExist` reaches a concrete path to misreporting of prices and/or data by breaking the invariant that external-initiator and bridge identities must stay bound to the correct auth material and name, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/bridge_types_controller.go::ValidateBridgeTypeNotExist
- Entrypoint: bridge or external-initiator REST path
- Attacker controls: job-owned bridge names, dot IDs, and cached response slots
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: external-initiator and bridge identities must stay bound to the correct auth material and name
- Expected Immunefi impact: misreporting of prices and/or data
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
