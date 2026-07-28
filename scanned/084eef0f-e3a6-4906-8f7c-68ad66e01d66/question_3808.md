# Q3808: Eventstore confirmed query - session persistence queue starvation

## Question
When an unprivileged actor start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`, does `GetNonExpiredConfirmedEvents` remain safe if they control persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry, or can that make it starve later outbounds or permanently jam the signing queue with one attacker-controlled flow, violate the rule that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/eventstore/store.go:GetNonExpiredConfirmedEvents
- Entrypoint: start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`
- Attacker controls: persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry
- Exploit idea: starve later outbounds or permanently jam the signing queue with one attacker-controlled flow
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
