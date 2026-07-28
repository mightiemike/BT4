# Q0040: Coordinator sign setup - nonce assignment queue starvation

## Question
If a user submit many public Push-chain actions that create concurrent outbounds to the same destination chain, can `createSignSetup` be pushed into a path where chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing causes it to starve later outbounds or permanently jam the signing queue with one attacker-controlled flow, so that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:createSignSetup
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing
- Exploit idea: starve later outbounds or permanently jam the signing queue with one attacker-controlled flow
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
