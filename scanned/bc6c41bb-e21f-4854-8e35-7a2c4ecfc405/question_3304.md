# Q3304: Event dedupe insert - event identity terminal mismatch

## Question
When an unprivileged actor repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks, does `InsertEventIfNotExists` remain safe if they control `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data, or can that make it mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts, violate the rule that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/chain_store.go:InsertEventIfNotExists
- Entrypoint: repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: replay the same inbound or outbound and verify every state transition is idempotent rather than generating conflicting rows
