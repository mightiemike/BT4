# Q2109: Boundary preservation edge case in ValidateExternalInitiator #3

## Question
Can an unprivileged attacker use adapter response bytes, cache keys, and retry/size behavior at `bridge or external-initiator REST path` so `ValidateExternalInitiator` reaches a concrete path to authentication bypass into bridge or external-initiator privileged behavior by breaking the invariant that adapter output and caches must not let one attacker-controlled request influence another job or principal, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/external_initiators_controller.go::ValidateExternalInitiator
- Entrypoint: bridge or external-initiator REST path
- Attacker controls: adapter response bytes, cache keys, and retry/size behavior
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: adapter output and caches must not let one attacker-controlled request influence another job or principal
- Expected Immunefi impact: authentication bypass into bridge or external-initiator privileged behavior
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
