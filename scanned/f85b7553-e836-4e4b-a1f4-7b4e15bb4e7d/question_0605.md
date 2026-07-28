# Q0605: Coordinator tx build - sign setup data recovered double-sign

## Question
Can an unprivileged attacker submit many public Push-chain actions that create concurrent outbounds to the same destination chain and use control over the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data so that `buildSignTransaction` recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states, breaking the invariant that nonce, signature, and eventstore state always belong to exactly one outbound at a time and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:buildSignTransaction
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states
- Invariant to test: nonce, signature, and eventstore state always belong to exactly one outbound at a time
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: compare coordinator-built signing requests with sessionmanager verification output for the same outbound under edge-case fields
