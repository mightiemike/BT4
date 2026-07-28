# Q2878: Signed nonce read - signed payload stuck broadcast

## Question
When an unprivileged actor cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction, does `ReadSignedNonce` remain safe if they control the persisted signed hash and signature bytes carried through rebroadcast and resolution, or can that make it leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure, violate the rule that an outbound is rewound only when replaying it cannot create double execution or contradict chain truth, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txflow/parse.go:ReadSignedNonce
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: the persisted signed hash and signature bytes carried through rebroadcast and resolution
- Exploit idea: leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure
- Invariant to test: an outbound is rewound only when replaying it cannot create double execution or contradict chain truth
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: submit same-chain traffic that changes finalized nonce and verify the resolver never attributes foreign nonce movement to the wrong outbound
