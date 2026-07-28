# Q0528: Signed nonce read - nonce view false revert

## Question
When an unprivileged actor create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access, does `ReadSignedNonce` remain safe if they control the signed nonce, finalized nonce, and pending nonce visible to the retry logic, or can that make it vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere, violate the rule that an outbound is rewound only when replaying it cannot create double execution or contradict chain truth, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/txflow/parse.go:ReadSignedNonce
- Entrypoint: create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access
- Attacker controls: the signed nonce, finalized nonce, and pending nonce visible to the retry logic
- Exploit idea: vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere
- Invariant to test: an outbound is rewound only when replaying it cannot create double execution or contradict chain truth
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: exercise success, revert, not-found, empty-hash, and nonce-consumed cases, then assert the resolver and broadcaster always pick the same safe terminal path
