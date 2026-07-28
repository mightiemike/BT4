# Q2838: Inbound build - cleanup horizon premature delete

## Question
If a user create a public Push-chain action that produces a pending outbound observed by the Universal Client, can `constructInbound` be pushed into a path where the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried causes it to delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, so that one user-visible bridge action can have at most one authoritative live row at a time no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: one user-visible bridge action can have at most one authoritative live row at a time
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: crash after each state transition, restart, and check whether the recovered row still matches the original source event and terminal outcome
