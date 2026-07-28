# Q0582: Inbound build - payload row premature delete

## Question
When an unprivileged actor submit a normal inbound transfer whose parsed event reaches the local event database, does `constructInbound` remain safe if they control the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic, or can that make it delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, violate the rule that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: crash after each state transition, restart, and check whether the recovered row still matches the original source event and terminal outcome
