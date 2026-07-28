# Q0673: Event data transition - payload row terminal mismatch

## Question
When an unprivileged actor submit a normal inbound transfer whose parsed event reaches the local event database, does `UpdateStatusAndEventData` remain safe if they control the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic, or can that make it mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts, violate the rule that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/chain_store.go:UpdateStatusAndEventData
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
