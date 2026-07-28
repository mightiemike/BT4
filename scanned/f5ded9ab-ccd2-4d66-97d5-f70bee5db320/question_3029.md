# Q3029: Event cleaner pass - event identity race overwrite

## Question
When an unprivileged actor repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks, does `performCleanup` remain safe if they control `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data, or can that make it overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, violate the rule that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_cleaner.go:performCleanup
- Entrypoint: repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: run two validators or two workers against the same flow, then inspect sqlite rows for duplicate `EventID`s, stale status writes, or missing `vote_tx_hash` values
