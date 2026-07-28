# Q3492: Event dedupe insert - payload row dedupe bypass

## Question
Can an unprivileged attacker repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks and use control over the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic so that `InsertEventIfNotExists` bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes, breaking the invariant that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/chain_store.go:InsertEventIfNotExists
- Entrypoint: repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: run two validators or two workers against the same flow, then inspect sqlite rows for duplicate `EventID`s, stale status writes, or missing `vote_tx_hash` values
