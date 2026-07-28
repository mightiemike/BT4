# Q3593: Event cleaner pass - payload row premature delete

## Question
Can an unprivileged attacker repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks and use control over the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic so that `performCleanup` delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, breaking the invariant that restarts and retries do not change the economic meaning of an event that is already in flight and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_cleaner.go:performCleanup
- Entrypoint: repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: restarts and retries do not change the economic meaning of an event that is already in flight
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: crash after each state transition, restart, and check whether the recovered row still matches the original source event and terminal outcome
