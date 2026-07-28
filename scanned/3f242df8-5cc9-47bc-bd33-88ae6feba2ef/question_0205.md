# Q0205: Event cleanup delete - event identity premature delete

## Question
If a user submit a normal inbound transfer whose parsed event reaches the local event database, can `DeleteTerminalEvents` be pushed into a path where `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data causes it to delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, so that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/chain_store.go:DeleteTerminalEvents
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
