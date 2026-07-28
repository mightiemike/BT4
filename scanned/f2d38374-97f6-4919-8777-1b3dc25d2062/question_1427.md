# Q1427: Event cleanup delete - cleanup horizon terminal mismatch

## Question
When an unprivileged actor submit a normal inbound transfer whose parsed event reaches the local event database, does `DeleteTerminalEvents` remain safe if they control the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried, or can that make it mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts, violate the rule that one user-visible bridge action can have at most one authoritative live row at a time, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/chain_store.go:DeleteTerminalEvents
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts
- Invariant to test: one user-visible bridge action can have at most one authoritative live row at a time
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: run two validators or two workers against the same flow, then inspect sqlite rows for duplicate `EventID`s, stale status writes, or missing `vote_tx_hash` values
