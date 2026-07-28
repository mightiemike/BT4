# Q2015: Coordinator tx build - sign setup data cross-event nonce reuse

## Question
Can an unprivileged attacker create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls and use control over the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data so that `buildSignTransaction` cause one outbound to reuse or consume signing state that should belong to a different outbound, breaking the invariant that nonce, signature, and eventstore state always belong to exactly one outbound at a time and leading to widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:buildSignTransaction
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: cause one outbound to reuse or consume signing state that should belong to a different outbound
- Invariant to test: nonce, signature, and eventstore state always belong to exactly one outbound at a time
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: shorten deadlines while slowing broadcast or resolution and see whether one crafted outbound can trap many others in nonterminal states
