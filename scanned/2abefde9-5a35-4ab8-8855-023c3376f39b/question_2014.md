# Q2014: Coordinator sign setup - sign setup data cross-event nonce reuse

## Question
When an unprivileged actor create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, does `createSignSetup` remain safe if they control the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data, or can that make it cause one outbound to reuse or consume signing state that should belong to a different outbound, violate the rule that nonce, signature, and eventstore state always belong to exactly one outbound at a time, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:createSignSetup
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: cause one outbound to reuse or consume signing state that should belong to a different outbound
- Invariant to test: nonce, signature, and eventstore state always belong to exactly one outbound at a time
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: shorten deadlines while slowing broadcast or resolution and see whether one crafted outbound can trap many others in nonterminal states
