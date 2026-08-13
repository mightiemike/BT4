# Q2048: Boundary preservation edge case in Show #2

## Question
Can an unprivileged attacker use external-initiator name, URL, and generated auth-token pairing at `GET /v2/bridge_types/:BridgeName` so `Show` reaches a concrete path to execute arbitrary system commands if adapter or job execution becomes attacker-controlled by breaking the invariant that external-initiator and bridge identities must stay bound to the correct auth material and name, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/bridge_types_controller.go::Show
- Entrypoint: GET /v2/bridge_types/:BridgeName
- Attacker controls: external-initiator name, URL, and generated auth-token pairing
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: external-initiator and bridge identities must stay bound to the correct auth material and name
- Expected Immunefi impact: execute arbitrary system commands if adapter or job execution becomes attacker-controlled
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
