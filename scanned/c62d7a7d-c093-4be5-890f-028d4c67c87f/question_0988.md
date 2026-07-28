# Q0988: Eventstore confirmed query - session persistence recovered double-sign

## Question
When an unprivileged actor submit many public Push-chain actions that create concurrent outbounds to the same destination chain, does `GetNonExpiredConfirmedEvents` remain safe if they control persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry, or can that make it recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states, violate the rule that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/eventstore/store.go:GetNonExpiredConfirmedEvents
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry
- Exploit idea: recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
