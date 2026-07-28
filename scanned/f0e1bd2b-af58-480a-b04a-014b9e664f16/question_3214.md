# Q3214: Inbound build - event identity premature delete

## Question
If a user repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks, can `constructInbound` be pushed into a path where `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data causes it to delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, so that one user-visible bridge action can have at most one authoritative live row at a time no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: one user-visible bridge action can have at most one authoritative live row at a time
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
