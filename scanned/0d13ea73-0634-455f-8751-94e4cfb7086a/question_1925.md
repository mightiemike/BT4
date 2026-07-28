# Q1925: Session outbound verify - sign setup data queue starvation

## Question
When an unprivileged actor create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, does `verifyOutboundSigningRequest` remain safe if they control the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data, or can that make it starve later outbounds or permanently jam the signing queue with one attacker-controlled flow, violate the rule that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/sessionmanager/sessionmanager.go:verifyOutboundSigningRequest
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: starve later outbounds or permanently jam the signing queue with one attacker-controlled flow
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: compare coordinator-built signing requests with sessionmanager verification output for the same outbound under edge-case fields
