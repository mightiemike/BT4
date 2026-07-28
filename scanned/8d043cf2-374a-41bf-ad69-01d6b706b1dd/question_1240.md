# Q1240: Inbound build - cleanup horizon dedupe bypass

## Question
Can an unprivileged attacker submit a normal inbound transfer whose parsed event reaches the local event database and use control over the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried so that `constructInbound` bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes, breaking the invariant that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
