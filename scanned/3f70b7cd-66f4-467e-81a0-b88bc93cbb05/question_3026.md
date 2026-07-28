# Q3026: Inbound build - event identity race overwrite

## Question
Can an unprivileged attacker repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks and use control over `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data so that `constructInbound` overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, breaking the invariant that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: run two validators or two workers against the same flow, then inspect sqlite rows for duplicate `EventID`s, stale status writes, or missing `vote_tx_hash` values
