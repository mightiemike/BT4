# Q0621: Signing-data decode - nonce view stuck broadcast

## Question
When an unprivileged actor create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access, does `DecodeSigningData` remain safe if they control the signed nonce, finalized nonce, and pending nonce visible to the retry logic, or can that make it leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure, violate the rule that refund or revert voting happens only after the client has enough evidence the intended outbound will not execute, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/txflow/parse.go:DecodeSigningData
- Entrypoint: create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access
- Attacker controls: the signed nonce, finalized nonce, and pending nonce visible to the retry logic
- Exploit idea: leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure
- Invariant to test: refund or revert voting happens only after the client has enough evidence the intended outbound will not execute
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force a dropped or replaced transaction on a local EVM chain and see whether the same outbound is incorrectly refunded, replayed, or duplicated
