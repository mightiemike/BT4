# Q0046: Session signing complete - nonce assignment queue starvation

## Question
Can an unprivileged attacker submit many public Push-chain actions that create concurrent outbounds to the same destination chain and use control over chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing so that `handleSigningComplete` starve later outbounds or permanently jam the signing queue with one attacker-controlled flow, breaking the invariant that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/sessionmanager/sessionmanager.go:handleSigningComplete
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing
- Exploit idea: starve later outbounds or permanently jam the signing queue with one attacker-controlled flow
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
