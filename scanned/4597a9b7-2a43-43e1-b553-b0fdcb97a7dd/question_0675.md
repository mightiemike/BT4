# Q0675: Event cleanup delete - payload row terminal mismatch

## Question
If a user submit a normal inbound transfer whose parsed event reaches the local event database, can `DeleteTerminalEvents` be pushed into a path where the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic causes it to mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts, so that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/chain_store.go:DeleteTerminalEvents
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
