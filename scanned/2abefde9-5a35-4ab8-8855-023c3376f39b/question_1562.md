# Q1562: Signed nonce read - broadcast state wrong rewind

## Question
Can an unprivileged attacker cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction and use control over `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound so that `ReadSignedNonce` rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution, breaking the invariant that refund or revert voting happens only after the client has enough evidence the intended outbound will not execute and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/txflow/parse.go:ReadSignedNonce
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound
- Exploit idea: rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution
- Invariant to test: refund or revert voting happens only after the client has enough evidence the intended outbound will not execute
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: exercise success, revert, not-found, empty-hash, and nonce-consumed cases, then assert the resolver and broadcaster always pick the same safe terminal path
