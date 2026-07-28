# Q3065: Signing-data decode - broadcast state wrong rewind

## Question
Can an unprivileged attacker trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing and use control over `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound so that `DecodeSigningData` rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution, breaking the invariant that normal user outbounds eventually reach a correct terminal state instead of looping forever and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing
- Attacker controls: `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound
- Exploit idea: rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution
- Invariant to test: normal user outbounds eventually reach a correct terminal state instead of looping forever
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: force a dropped or replaced transaction on a local EVM chain and see whether the same outbound is incorrectly refunded, replayed, or duplicated
