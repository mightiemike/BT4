# Q1937: Signing-data decode - nonce view wrong rewind

## Question
If a user cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction, can `DecodeSigningData` be pushed into a path where the signed nonce, finalized nonce, and pending nonce visible to the retry logic causes it to rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution, so that an outbound is rewound only when replaying it cannot create double execution or contradict chain truth no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: the signed nonce, finalized nonce, and pending nonce visible to the retry logic
- Exploit idea: rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution
- Invariant to test: an outbound is rewound only when replaying it cannot create double execution or contradict chain truth
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: submit same-chain traffic that changes finalized nonce and verify the resolver never attributes foreign nonce movement to the wrong outbound
