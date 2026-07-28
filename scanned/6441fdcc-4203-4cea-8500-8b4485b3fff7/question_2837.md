# Q2837: Event cleanup delete - cleanup horizon premature delete

## Question
When an unprivileged actor create a public Push-chain action that produces a pending outbound observed by the Universal Client, does `DeleteTerminalEvents` remain safe if they control the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried, or can that make it delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, violate the rule that one user-visible bridge action can have at most one authoritative live row at a time, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/common/chain_store.go:DeleteTerminalEvents
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: one user-visible bridge action can have at most one authoritative live row at a time
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: crash after each state transition, restart, and check whether the recovered row still matches the original source event and terminal outcome
