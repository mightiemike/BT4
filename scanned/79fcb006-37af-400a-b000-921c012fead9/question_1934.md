# Q1934: Resolver outbound route - nonce view wrong rewind

## Question
When an unprivileged actor cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction, does `resolveOutbound` remain safe if they control the signed nonce, finalized nonce, and pending nonce visible to the retry logic, or can that make it rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution, violate the rule that an outbound is rewound only when replaying it cannot create double execution or contradict chain truth, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/txresolver/resolver.go:resolveOutbound
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: the signed nonce, finalized nonce, and pending nonce visible to the retry logic
- Exploit idea: rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution
- Invariant to test: an outbound is rewound only when replaying it cannot create double execution or contradict chain truth
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: submit same-chain traffic that changes finalized nonce and verify the resolver never attributes foreign nonce movement to the wrong outbound
