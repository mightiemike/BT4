# Q0207: Inbound vote path - event identity premature delete

## Question
If a user submit a normal inbound transfer whose parsed event reaches the local event database, can `processInboundEvent` be pushed into a path where `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data causes it to delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, so that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/event_processor.go:processInboundEvent
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
