# Q3398: Event dedupe insert - payload row race overwrite

## Question
When an unprivileged actor repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks, does `InsertEventIfNotExists` remain safe if they control the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic, or can that make it overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, violate the rule that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/chain_store.go:InsertEventIfNotExists
- Entrypoint: repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: replay the same inbound or outbound and verify every state transition is idempotent rather than generating conflicting rows
