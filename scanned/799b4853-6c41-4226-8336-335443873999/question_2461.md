# Q2461: Event cleanup delete - status machine premature delete

## Question
Can an unprivileged attacker create a public Push-chain action that produces a pending outbound observed by the Universal Client and use control over status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED` so that `DeleteTerminalEvents` delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, breaking the invariant that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data and leading to widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/common/chain_store.go:DeleteTerminalEvents
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
