# Q1843: Signing-data decode - broadcast state foreign nonce consume

## Question
If a user cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction, can `DecodeSigningData` be pushed into a path where `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound causes it to let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality, so that an outbound is rewound only when replaying it cannot create double execution or contradict chain truth no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound
- Exploit idea: let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality
- Invariant to test: an outbound is rewound only when replaying it cannot create double execution or contradict chain truth
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: submit same-chain traffic that changes finalized nonce and verify the resolver never attributes foreign nonce movement to the wrong outbound
