# Q3706: Coordinator sign setup - sign setup data verification split

## Question
If a user start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`, can `createSignSetup` be pushed into a path where the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data causes it to make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign, so that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:createSignSetup
- Entrypoint: start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
