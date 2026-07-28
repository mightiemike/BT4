# Q0901: EVM resolve path - receipt outcome false revert

## Question
If a user create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access, can `resolveOutboundEVM` be pushed into a path where whether the destination receipt is not found, insufficiently confirmed, reverted, or successful causes it to vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere, so that nonce-based resolution decisions are tied to the intended outbound rather than unrelated same-signer traffic no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/txresolver/evm.go:resolveOutboundEVM
- Entrypoint: create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access
- Attacker controls: whether the destination receipt is not found, insufficiently confirmed, reverted, or successful
- Exploit idea: vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere
- Invariant to test: nonce-based resolution decisions are tied to the intended outbound rather than unrelated same-signer traffic
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: submit same-chain traffic that changes finalized nonce and verify the resolver never attributes foreign nonce movement to the wrong outbound
