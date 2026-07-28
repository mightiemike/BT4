# Q0202: Event dedupe insert - event identity premature delete

## Question
Can an unprivileged attacker submit a normal inbound transfer whose parsed event reaches the local event database and use control over `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data so that `InsertEventIfNotExists` delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, breaking the invariant that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/chain_store.go:InsertEventIfNotExists
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
