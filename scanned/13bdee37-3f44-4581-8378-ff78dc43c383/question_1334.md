# Q1334: Inbound build - cleanup horizon premature delete

## Question
If a user submit a normal inbound transfer whose parsed event reaches the local event database, can `constructInbound` be pushed into a path where the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried causes it to delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, so that restarts and retries do not change the economic meaning of an event that is already in flight no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: restarts and retries do not change the economic meaning of an event that is already in flight
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: replay the same inbound or outbound and verify every state transition is idempotent rather than generating conflicting rows
