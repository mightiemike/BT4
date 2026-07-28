# Q1560: EVM rewind loop - broadcast state wrong rewind

## Question
If a user cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction, can `rewindToSigned` be pushed into a path where `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound causes it to rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution, so that refund or revert voting happens only after the client has enough evidence the intended outbound will not execute no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/txresolver/evm.go:rewindToSigned
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound
- Exploit idea: rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution
- Invariant to test: refund or revert voting happens only after the client has enough evidence the intended outbound will not execute
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: exercise success, revert, not-found, empty-hash, and nonce-consumed cases, then assert the resolver and broadcaster always pick the same safe terminal path
