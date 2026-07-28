# Q0245: Signing-data decode - broadcast state stuck broadcast

## Question
Can an unprivileged attacker create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access and use control over `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound so that `DecodeSigningData` leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure, breaking the invariant that normal user outbounds eventually reach a correct terminal state instead of looping forever and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access
- Attacker controls: `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound
- Exploit idea: leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure
- Invariant to test: normal user outbounds eventually reach a correct terminal state instead of looping forever
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: trace one outbound through repeated `SIGNED`/`BROADCASTED` transitions and confirm it cannot loop forever under user-controlled inputs
