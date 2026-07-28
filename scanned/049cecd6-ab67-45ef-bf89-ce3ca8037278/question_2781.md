# Q2781: EVM resolve path - signed payload false revert

## Question
If a user cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction, can `resolveOutboundEVM` be pushed into a path where the persisted signed hash and signature bytes carried through rebroadcast and resolution causes it to vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere, so that nonce-based resolution decisions are tied to the intended outbound rather than unrelated same-signer traffic no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txresolver/evm.go:resolveOutboundEVM
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: the persisted signed hash and signature bytes carried through rebroadcast and resolution
- Exploit idea: vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere
- Invariant to test: nonce-based resolution decisions are tied to the intended outbound rather than unrelated same-signer traffic
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: trace one outbound through repeated `SIGNED`/`BROADCASTED` transitions and confirm it cannot loop forever under user-controlled inputs
