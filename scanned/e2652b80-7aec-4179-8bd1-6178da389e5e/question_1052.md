# Q1052: migrate_legacy_hardlinks charges far less than it costs (snapshot_utils.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `migrate_legacy_hardlinks` in `runtime/src/snapshot_utils.rs` with a path that consumes the resource before the meter is charged, and make the real CPU/memory cost of `migrate_legacy_hardlinks` exceed the units charged for it, so that the invariant "Charged cost is a monotone upper bound on real cost for every input shape." breaks and the result is DoS?

## Target
- File/function: `runtime/src/snapshot_utils.rs` -> `migrate_legacy_hardlinks()` (around line 1338)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: a path that consumes the resource before the meter is charged
- Exploit idea: Find the input shape where `migrate_legacy_hardlinks`'s real CPU/memory/IO cost grows much faster than the compute units or fee charged for it.
- Invariant to test: Charged cost is a monotone upper bound on real cost for every input shape.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Measure real time/allocations versus units charged across input sizes; assert cost/CU ratio stays bounded.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. An unprivileged attacker can cheaply create accounts or writes that force disproportionate storage, index, scan, hashing, or background-cleanup work and exhaust node memory or disk below true transaction cost.
