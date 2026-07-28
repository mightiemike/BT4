# Q1048: Event dedupe insert - status machine terminal mismatch

## Question
When an unprivileged actor submit a normal inbound transfer whose parsed event reaches the local event database, does `InsertEventIfNotExists` remain safe if they control status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`, or can that make it mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts, violate the rule that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/chain_store.go:InsertEventIfNotExists
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`
- Exploit idea: mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: crash after each state transition, restart, and check whether the recovered row still matches the original source event and terminal outcome
