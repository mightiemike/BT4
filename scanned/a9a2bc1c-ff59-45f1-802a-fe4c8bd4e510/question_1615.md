# Q1615: Event cleanup delete - event identity dedupe bypass

## Question
If a user create a public Push-chain action that produces a pending outbound observed by the Universal Client, can `DeleteTerminalEvents` be pushed into a path where `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data causes it to bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes, so that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/common/chain_store.go:DeleteTerminalEvents
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: `EventID`, `Type`, and `ConfirmationType` as derived from user-visible chain data
- Exploit idea: bypass local deduplication and make the same user action exist as multiple live rows with different downstream outcomes
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: replay the same inbound or outbound and verify every state transition is idempotent rather than generating conflicting rows
