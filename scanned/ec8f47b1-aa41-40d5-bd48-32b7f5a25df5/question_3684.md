# Q3684: Inbound build - payload row terminal mismatch

## Question
If a user repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks, can `constructInbound` be pushed into a path where the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic causes it to mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts, so that one user-visible bridge action can have at most one authoritative live row at a time no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts
- Invariant to test: one user-visible bridge action can have at most one authoritative live row at a time
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
