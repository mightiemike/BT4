# Q1239: Event cleanup delete - cleanup horizon dedupe bypass

## Question
If a user submit a normal inbound transfer whose parsed event reaches the local event database, can `DeleteTerminalEvents` be pushed into a path where the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried causes it to bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes, so that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/chain_store.go:DeleteTerminalEvents
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
