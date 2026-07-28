# Q3712: Session signing complete - sign setup data verification split

## Question
Can an unprivileged attacker start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED` and use control over the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data so that `handleSigningComplete` make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign, breaking the invariant that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/sessionmanager/sessionmanager.go:handleSigningComplete
- Entrypoint: start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
