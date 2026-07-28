# Q1655: Signing-data decode - broadcast state false revert

## Question
Can an unprivileged attacker cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction and use control over `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound so that `DecodeSigningData` vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere, breaking the invariant that normal user outbounds eventually reach a correct terminal state instead of looping forever and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound
- Exploit idea: vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere
- Invariant to test: normal user outbounds eventually reach a correct terminal state instead of looping forever
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: force a dropped or replaced transaction on a local EVM chain and see whether the same outbound is incorrectly refunded, replayed, or duplicated
