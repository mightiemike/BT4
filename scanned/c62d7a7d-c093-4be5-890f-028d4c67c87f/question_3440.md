# Q3440: EVM rewind loop - nonce view wrong rewind

## Question
If a user trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing, can `rewindToSigned` be pushed into a path where the signed nonce, finalized nonce, and pending nonce visible to the retry logic causes it to rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution, so that refund or revert voting happens only after the client has enough evidence the intended outbound will not execute no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/txresolver/evm.go:rewindToSigned
- Entrypoint: trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing
- Attacker controls: the signed nonce, finalized nonce, and pending nonce visible to the retry logic
- Exploit idea: rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution
- Invariant to test: refund or revert voting happens only after the client has enough evidence the intended outbound will not execute
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: exercise success, revert, not-found, empty-hash, and nonce-consumed cases, then assert the resolver and broadcaster always pick the same safe terminal path
