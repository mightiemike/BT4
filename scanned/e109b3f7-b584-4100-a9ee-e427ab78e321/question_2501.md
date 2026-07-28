# Q2501: Signing-data decode - receipt outcome stuck broadcast

## Question
When an unprivileged actor cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction, does `DecodeSigningData` remain safe if they control whether the destination receipt is not found, insufficiently confirmed, reverted, or successful, or can that make it leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure, violate the rule that refund or revert voting happens only after the client has enough evidence the intended outbound will not execute, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: whether the destination receipt is not found, insufficiently confirmed, reverted, or successful
- Exploit idea: leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure
- Invariant to test: refund or revert voting happens only after the client has enough evidence the intended outbound will not execute
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: exercise success, revert, not-found, empty-hash, and nonce-consumed cases, then assert the resolver and broadcaster always pick the same safe terminal path
