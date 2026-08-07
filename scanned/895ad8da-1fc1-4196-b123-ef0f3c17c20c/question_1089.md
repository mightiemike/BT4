# Q1089: get_program_kind amplifies a cheap input into expensive work (builtin_programs_filter.rs)

## Question
Can an unprivileged attacker entering through a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair reach `get_program_kind` in `compute-budget-instruction/src/builtin_programs_filter.rs` with a key that exists on an ancestor fork but not the current one, and make a minimal accepted input to `get_program_kind` fan out into disproportionate downstream work, so that the invariant "Work performed is proportional to the size and fee of the input that triggered it." breaks and the result is DoS?

## Target
- File/function: `compute-budget-instruction/src/builtin_programs_filter.rs` -> `get_program_kind()` (around line 37)
- Entrypoint: a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair
- Attacker controls: a key that exists on an ancestor fork but not the current one
- Exploit idea: Send the smallest accepted input that makes `get_program_kind` fan out into large downstream work, so a cheap transaction/packet costs the node orders more.
- Invariant to test: Work performed is proportional to the size and fee of the input that triggered it.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Plot input bytes versus work done in `get_program_kind`; assert the ratio is bounded by a constant.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. An unprivileged attacker can cheaply create accounts or writes that force disproportionate storage, index, scan, hashing, or background-cleanup work and exhaust node memory or disk below true transaction cost.
