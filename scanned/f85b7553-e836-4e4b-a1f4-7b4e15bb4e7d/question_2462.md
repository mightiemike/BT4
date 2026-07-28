# Q2462: Inbound build - status machine premature delete

## Question
When an unprivileged actor create a public Push-chain action that produces a pending outbound observed by the Universal Client, does `constructInbound` remain safe if they control status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`, or can that make it delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck, violate the rule that rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`
- Exploit idea: delete or age out a live event before it reaches a safe terminal state, leaving funds or refunds stuck
- Invariant to test: rows become terminal only after the event has been safely voted, executed, or reverted with matching payload data
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
