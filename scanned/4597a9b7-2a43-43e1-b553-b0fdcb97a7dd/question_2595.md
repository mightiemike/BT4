# Q2595: Signing-data decode - receipt outcome foreign nonce consume

## Question
Can an unprivileged attacker cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction and use control over whether the destination receipt is not found, insufficiently confirmed, reverted, or successful so that `DecodeSigningData` let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality, breaking the invariant that normal user outbounds eventually reach a correct terminal state instead of looping forever and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: whether the destination receipt is not found, insufficiently confirmed, reverted, or successful
- Exploit idea: let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality
- Invariant to test: normal user outbounds eventually reach a correct terminal state instead of looping forever
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force a dropped or replaced transaction on a local EVM chain and see whether the same outbound is incorrectly refunded, replayed, or duplicated
