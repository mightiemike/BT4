# Q1920: Coordinator sign setup - sign setup data queue starvation

## Question
If a user create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, can `createSignSetup` be pushed into a path where the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data causes it to starve later outbounds or permanently jam the signing queue with one attacker-controlled flow, so that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:createSignSetup
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: starve later outbounds or permanently jam the signing queue with one attacker-controlled flow
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: compare coordinator-built signing requests with sessionmanager verification output for the same outbound under edge-case fields
