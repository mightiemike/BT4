# Q1183: EVM resolve path - signed payload wrong rewind

## Question
When an unprivileged actor create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access, does `resolveOutboundEVM` remain safe if they control the persisted signed hash and signature bytes carried through rebroadcast and resolution, or can that make it rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution, violate the rule that refund or revert voting happens only after the client has enough evidence the intended outbound will not execute, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/txresolver/evm.go:resolveOutboundEVM
- Entrypoint: create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access
- Attacker controls: the persisted signed hash and signature bytes carried through rebroadcast and resolution
- Exploit idea: rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution
- Invariant to test: refund or revert voting happens only after the client has enough evidence the intended outbound will not execute
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: force a dropped or replaced transaction on a local EVM chain and see whether the same outbound is incorrectly refunded, replayed, or duplicated
