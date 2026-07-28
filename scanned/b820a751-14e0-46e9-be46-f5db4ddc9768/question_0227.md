# Q0227: Coordinator assignment - nonce assignment recovered double-sign

## Question
Can an unprivileged attacker submit many public Push-chain actions that create concurrent outbounds to the same destination chain and use control over chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing so that `processEventAsCoordinator` recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states, breaking the invariant that restart recovery never changes the signed meaning or multiplicity of an outbound already in flight and leading to widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:processEventAsCoordinator
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing
- Exploit idea: recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states
- Invariant to test: restart recovery never changes the signed meaning or multiplicity of an outbound already in flight
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: shorten deadlines while slowing broadcast or resolution and see whether one crafted outbound can trap many others in nonterminal states
