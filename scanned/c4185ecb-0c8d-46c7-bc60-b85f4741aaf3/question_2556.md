# Q2556: Inbound build - status machine terminal mismatch

## Question
Can an unprivileged attacker create a public Push-chain action that produces a pending outbound observed by the Universal Client and use control over status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED` so that `constructInbound` mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts, breaking the invariant that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`
- Exploit idea: mark an event terminal with a mismatched payload or missing vote hash so retries or refunds resolve against the wrong facts
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: replay the same inbound or outbound and verify every state transition is idempotent rather than generating conflicting rows
