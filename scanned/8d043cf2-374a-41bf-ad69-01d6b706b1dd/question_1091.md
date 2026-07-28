# Q1091: Signing-data decode - receipt outcome foreign nonce consume

## Question
When an unprivileged actor create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access, does `DecodeSigningData` remain safe if they control whether the destination receipt is not found, insufficiently confirmed, reverted, or successful, or can that make it let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality, violate the rule that refund or revert voting happens only after the client has enough evidence the intended outbound will not execute, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access
- Attacker controls: whether the destination receipt is not found, insufficiently confirmed, reverted, or successful
- Exploit idea: let one attacker-crafted outbound inherit the nonce fate of a different transaction and resolve against the wrong chain reality
- Invariant to test: refund or revert voting happens only after the client has enough evidence the intended outbound will not execute
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: force a dropped or replaced transaction on a local EVM chain and see whether the same outbound is incorrectly refunded, replayed, or duplicated
