# Q1243: Event cleaner pass - cleanup horizon dedupe bypass

## Question
When an unprivileged actor submit a normal inbound transfer whose parsed event reaches the local event database, does `performCleanup` remain safe if they control the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried, or can that make it bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes, violate the rule that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_cleaner.go:performCleanup
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
