# Q2085: Event cleanup delete - payload row premature delete

## Question
If a user create a public Push-chain action that produces a pending outbound observed by the Universal Client, can `DeleteTerminalEvents` be pushed into a path where the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic causes it to delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, so that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/common/chain_store.go:DeleteTerminalEvents
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: the exact `EventData` JSON and any `vote_tx_hash` written after processing attacker-controlled traffic
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: replay the same inbound or outbound and verify every state transition is idempotent rather than generating conflicting rows
