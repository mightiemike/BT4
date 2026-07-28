# Q2968: Resolver outbound route - signed payload foreign nonce consume

## Question
Can an unprivileged attacker cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction and use control over the persisted signed hash and signature bytes carried through rebroadcast and resolution so that `resolveOutbound` let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality, breaking the invariant that refund or revert voting happens only after the client has enough evidence the intended outbound will not execute and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/txresolver/resolver.go:resolveOutbound
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: the persisted signed hash and signature bytes carried through rebroadcast and resolution
- Exploit idea: let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality
- Invariant to test: refund or revert voting happens only after the client has enough evidence the intended outbound will not execute
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: exercise success, revert, not-found, empty-hash, and nonce-consumed cases, then assert the resolver and broadcaster always pick the same safe terminal path
