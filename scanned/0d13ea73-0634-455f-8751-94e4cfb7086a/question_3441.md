# Q3441: Signing-data decode - nonce view wrong rewind

## Question
When an unprivileged actor trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing, does `DecodeSigningData` remain safe if they control the signed nonce, finalized nonce, and pending nonce visible to the retry logic, or can that make it rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution, violate the rule that refund or revert voting happens only after the client has enough evidence the intended outbound will not execute, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing
- Attacker controls: the signed nonce, finalized nonce, and pending nonce visible to the retry logic
- Exploit idea: rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution
- Invariant to test: refund or revert voting happens only after the client has enough evidence the intended outbound will not execute
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: exercise success, revert, not-found, empty-hash, and nonce-consumed cases, then assert the resolver and broadcaster always pick the same safe terminal path
