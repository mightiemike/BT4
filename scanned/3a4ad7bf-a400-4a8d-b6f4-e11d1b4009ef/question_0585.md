# Q0585: Event cleaner pass - payload row premature delete

## Question
If a user submit a normal inbound transfer whose parsed event reaches the local event database, can `performCleanup` be pushed into a path where the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic causes it to delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, so that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/event_cleaner.go:performCleanup
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: crash after each state transition, restart, and check whether the recovered row still matches the original source event and terminal outcome
