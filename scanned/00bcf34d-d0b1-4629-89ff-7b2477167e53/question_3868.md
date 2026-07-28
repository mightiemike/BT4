# Q3868: Event dedupe insert - status machine dedupe bypass

## Question
When an unprivileged actor repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks, does `InsertEventIfNotExists` remain safe if they control status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`, or can that make it bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes, violate the rule that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/chain_store.go:InsertEventIfNotExists
- Entrypoint: repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks
- Attacker controls: status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`
- Exploit idea: bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: replay the same inbound or outbound and verify every state transition is idempotent rather than generating conflicting rows
