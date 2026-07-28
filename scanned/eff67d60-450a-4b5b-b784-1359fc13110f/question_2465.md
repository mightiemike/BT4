# Q2465: Event cleaner pass - status machine premature delete

## Question
If a user create a public Push-chain action that produces a pending outbound observed by the Universal Client, can `performCleanup` be pushed into a path where status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED` causes it to delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, so that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/common/event_cleaner.go:performCleanup
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
