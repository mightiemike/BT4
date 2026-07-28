# Q2219: Signing-data decode - nonce view foreign nonce consume

## Question
If a user cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction, can `DecodeSigningData` be pushed into a path where the signed nonce, finalized nonce, and pending nonce visible to the retry logic causes it to let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality, so that nonce-based resolution decisions are tied to the intended outbound rather than unrelated same-signer traffic no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: the signed nonce, finalized nonce, and pending nonce visible to the retry logic
- Exploit idea: let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality
- Invariant to test: nonce-based resolution decisions are tied to the intended outbound rather than unrelated same-signer traffic
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: trace one outbound through repeated `SIGNED`/`BROADCASTED` transitions and confirm it cannot loop forever under user-controlled inputs
