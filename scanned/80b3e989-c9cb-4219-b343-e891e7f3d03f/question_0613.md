# Q0613: Eventstore stale unsigned cleanup - sign setup data recovered double-sign

## Question
If a user submit many public Push-chain actions that create concurrent outbounds to the same destination chain, can `DeleteOldUnsignedEvents` be pushed into a path where the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data causes it to recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states, so that nonce, signature, and eventstore state always belong to exactly one outbound at a time no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/eventstore/store.go:DeleteOldUnsignedEvents
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states
- Invariant to test: nonce, signature, and eventstore state always belong to exactly one outbound at a time
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: compare coordinator-built signing requests with sessionmanager verification output for the same outbound under edge-case fields
