# Q3123: Event cleaner pass - event identity dedupe bypass

## Question
Can an unprivileged attacker repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks and use control over `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data so that `performCleanup` bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes, breaking the invariant that restarts and retries do not change the economic meaning of an event that is already in flight and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_cleaner.go:performCleanup
- Entrypoint: repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes
- Invariant to test: restarts and retries do not change the economic meaning of an event that is already in flight
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: crash after each state transition, restart, and check whether the recovered row still matches the original source event and terminal outcome
