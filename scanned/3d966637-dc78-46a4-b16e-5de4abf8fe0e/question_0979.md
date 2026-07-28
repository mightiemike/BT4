# Q0979: Coordinator assignment - session persistence recovered double-sign

## Question
If a user submit many public Push-chain actions that create concurrent outbounds to the same destination chain, can `processEventAsCoordinator` be pushed into a path where persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry causes it to recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states, so that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:processEventAsCoordinator
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry
- Exploit idea: recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
