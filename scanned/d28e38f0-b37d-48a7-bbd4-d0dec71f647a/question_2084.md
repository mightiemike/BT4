# Q2084: Event vote transition - payload row premature delete

## Question
When an unprivileged actor create a public Push-chain action that produces a pending outbound observed by the Universal Client, does `UpdateStatusAndVoteTxHash` remain safe if they control the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic, or can that make it delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, violate the rule that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/common/chain_store.go:UpdateStatusAndVoteTxHash
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: replay the same inbound or outbound and verify every state transition is idempotent rather than generating conflicting rows
