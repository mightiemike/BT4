# Q0715: Signing-data decode - nonce view foreign nonce consume

## Question
Can an unprivileged attacker create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access and use control over the signed nonce, finalized nonce, and pending nonce visible to the retry logic so that `DecodeSigningData` let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality, breaking the invariant that normal user outbounds eventually reach a correct terminal state instead of looping forever and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access
- Attacker controls: the signed nonce, finalized nonce, and pending nonce visible to the retry logic
- Exploit idea: let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality
- Invariant to test: normal user outbounds eventually reach a correct terminal state instead of looping forever
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: trace one outbound through repeated `SIGNED`/`BROADCASTED` transitions and confirm it cannot loop forever under user-controlled inputs
