# Q3431: Eventstore restart recover - sign setup data queue starvation

## Question
Can an unprivileged attacker start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED` and use control over the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data so that `RecoverInProgressEvents` starve later outbounds or permanently jam the signing queue with one attacker-controlled flow, breaking the invariant that nonce, signature, and eventstore state always belong to exactly one outbound at a time and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/eventstore/store.go:RecoverInProgressEvents
- Entrypoint: start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: starve later outbounds or permanently jam the signing queue with one attacker-controlled flow
- Invariant to test: nonce, signature, and eventstore state always belong to exactly one outbound at a time
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: compare coordinator-built signing requests with sessionmanager verification output for the same outbound under edge-case fields
