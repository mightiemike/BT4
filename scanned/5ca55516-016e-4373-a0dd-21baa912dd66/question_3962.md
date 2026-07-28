# Q3962: Event dedupe insert - status machine premature delete

## Question
Can an unprivileged attacker repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks and use control over status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED` so that `InsertEventIfNotExists` delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, breaking the invariant that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/chain_store.go:InsertEventIfNotExists
- Entrypoint: repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks
- Attacker controls: status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: run two validators or two workers against the same flow, then inspect sqlite rows for duplicate `EventID`s, stale status writes, or missing `vote_tx_hash` values
