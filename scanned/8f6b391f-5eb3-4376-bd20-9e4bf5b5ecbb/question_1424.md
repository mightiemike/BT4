# Q1424: Event dedupe insert - cleanup horizon terminal mismatch

## Question
If a user submit a normal inbound transfer whose parsed event reaches the local event database, can `InsertEventIfNotExists` be pushed into a path where the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried causes it to mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts, so that one user-visible bridge action can have at most one authoritative live row at a time no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/common/chain_store.go:InsertEventIfNotExists
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts
- Invariant to test: one user-visible bridge action can have at most one authoritative live row at a time
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: run two validators or two workers against the same flow, then inspect sqlite rows for duplicate `EventID`s, stale status writes, or missing `vote_tx_hash` values
