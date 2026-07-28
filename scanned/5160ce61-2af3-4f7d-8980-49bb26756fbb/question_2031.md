# Q2031: Signing-data decode - nonce view false revert

## Question
When an unprivileged actor cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction, does `DecodeSigningData` remain safe if they control the signed nonce, finalized nonce, and pending nonce visible to the retry logic, or can that make it vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere, violate the rule that refund or revert voting happens only after the client has enough evidence the intended outbound will not execute, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: the signed nonce, finalized nonce, and pending nonce visible to the retry logic
- Exploit idea: vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere
- Invariant to test: refund or revert voting happens only after the client has enough evidence the intended outbound will not execute
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: exercise success, revert, not-found, empty-hash, and nonce-consumed cases, then assert the resolver and broadcaster always pick the same safe terminal path
