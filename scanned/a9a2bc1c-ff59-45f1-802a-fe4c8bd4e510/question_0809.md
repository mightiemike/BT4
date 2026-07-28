# Q0809: Signing-data decode - receipt outcome wrong rewind

## Question
Can an unprivileged attacker create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access and use control over whether the destination receipt is not found, insufficiently confirmed, reverted, or successful so that `DecodeSigningData` rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution, breaking the invariant that normal user outbounds eventually reach a correct terminal state instead of looping forever and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access
- Attacker controls: whether the destination receipt is not found, insufficiently confirmed, reverted, or successful
- Exploit idea: rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution
- Invariant to test: normal user outbounds eventually reach a correct terminal state instead of looping forever
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: trace one outbound through repeated `SIGNED`/`BROADCASTED` transitions and confirm it cannot loop forever under user-controlled inputs
