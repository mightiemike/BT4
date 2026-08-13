# Q2108: Boundary preservation edge case in ValidateExternalInitiator #2

## Question
Can an unprivileged attacker use external-initiator name, URL, and generated auth-token pairing at `bridge or external-initiator REST path` so `ValidateExternalInitiator` reaches a concrete path to execute arbitrary system commands if adapter or job execution becomes attacker-controlled by breaking the invariant that external-initiator and bridge identities must stay bound to the correct auth material and name, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/external_initiators_controller.go::ValidateExternalInitiator
- Entrypoint: bridge or external-initiator REST path
- Attacker controls: external-initiator name, URL, and generated auth-token pairing
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: external-initiator and bridge identities must stay bound to the correct auth material and name
- Expected Immunefi impact: execute arbitrary system commands if adapter or job execution becomes attacker-controlled
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
