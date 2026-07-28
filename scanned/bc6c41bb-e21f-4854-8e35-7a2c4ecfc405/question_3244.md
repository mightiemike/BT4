# Q3244: Eventstore confirmed query - nonce assignment recovered double-sign

## Question
When an unprivileged actor start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`, does `GetNonExpiredConfirmedEvents` remain safe if they control chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing, or can that make it recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states, violate the rule that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/eventstore/store.go:GetNonExpiredConfirmedEvents
- Entrypoint: start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`
- Attacker controls: chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing
- Exploit idea: recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
