# Q3777: Event cleanup delete - status machine race overwrite

## Question
When an unprivileged actor repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks, does `DeleteTerminalEvents` remain safe if they control status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`, or can that make it overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, violate the rule that one user-visible bridge action can have at most one authoritative live row at a time, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/chain_store.go:DeleteTerminalEvents
- Entrypoint: repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks
- Attacker controls: status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: one user-visible bridge action can have at most one authoritative live row at a time
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
