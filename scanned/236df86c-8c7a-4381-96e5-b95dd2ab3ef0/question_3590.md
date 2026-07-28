# Q3590: Inbound build - payload row premature delete

## Question
If a user repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks, can `constructInbound` be pushed into a path where the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic causes it to delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, so that restarts and retries do not change the economic meaning of an event that is already in flight no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: repeat a user-reachable cross-chain flow until the same event is retried across listener, confirmer, broadcaster, or resolver ticks
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: restarts and retries do not change the economic meaning of an event that is already in flight
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: crash after each state transition, restart, and check whether the recovered row still matches the original source event and terminal outcome
