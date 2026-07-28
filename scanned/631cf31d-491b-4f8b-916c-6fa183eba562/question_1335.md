# Q1335: Inbound vote path - cleanup horizon premature delete

## Question
When an unprivileged actor submit a normal inbound transfer whose parsed event reaches the local event database, does `processInboundEvent` remain safe if they control the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried, or can that make it delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, violate the rule that restarts and retries do not change the economic meaning of an event that is already in flight, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_processor.go:processInboundEvent
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: restarts and retries do not change the economic meaning of an event that is already in flight
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: replay the same inbound or outbound and verify every state transition is idempotent rather than generating conflicting rows
