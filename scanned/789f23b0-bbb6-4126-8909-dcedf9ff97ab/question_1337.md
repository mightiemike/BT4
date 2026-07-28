# Q1337: Event cleaner pass - cleanup horizon premature delete

## Question
Can an unprivileged attacker submit a normal inbound transfer whose parsed event reaches the local event database and use control over the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried so that `performCleanup` delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, breaking the invariant that restarts and retries do not change the economic meaning of an event that is already in flight and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_cleaner.go:performCleanup
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: restarts and retries do not change the economic meaning of an event that is already in flight
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: replay the same inbound or outbound and verify every state transition is idempotent rather than generating conflicting rows
