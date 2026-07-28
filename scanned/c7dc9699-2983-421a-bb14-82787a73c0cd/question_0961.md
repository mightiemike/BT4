# Q0961: Event cleaner pass - status machine premature delete

## Question
Can an unprivileged attacker submit a normal inbound transfer whose parsed event reaches the local event database and use control over status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED` so that `performCleanup` delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, breaking the invariant that one user-visible bridge action can have at most one authoritative live row at a time and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_cleaner.go:performCleanup
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: one user-visible bridge action can have at most one authoritative live row at a time
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: run two validators or two workers against the same flow, then inspect sqlite rows for duplicate `EventID`s, stale status writes, or missing `vote_tx_hash` values
