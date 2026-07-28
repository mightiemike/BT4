# Q2930: Event vote transition - cleanup horizon terminal mismatch

## Question
If a user create a public Push-chain action that produces a pending outbound observed by the Universal Client, can `UpdateStatusAndVoteTxHash` be pushed into a path where the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried causes it to mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts, so that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/common/chain_store.go:UpdateStatusAndVoteTxHash
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
