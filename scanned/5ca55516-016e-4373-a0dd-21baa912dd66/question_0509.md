# Q0509: Coordinator assignment - sign setup data cross-event nonce reuse

## Question
If a user submit many public Push-chain actions that create concurrent outbounds to the same destination chain, can `processEventAsCoordinator` be pushed into a path where the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data causes it to cause one outbound to reuse or consume signing state that should belong to a different outbound, so that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:processEventAsCoordinator
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: cause one outbound to reuse or consume signing state that should belong to a different outbound
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
