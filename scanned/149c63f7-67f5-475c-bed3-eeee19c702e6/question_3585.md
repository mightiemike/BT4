# Q3585: get_non_rooted amplifies a cheap input into expensive work (bank_forks.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `get_non_rooted` in `runtime/src/bank_forks.rs` with a key that exists on an ancestor fork but not the current one, and make a minimal accepted input to `get_non_rooted` fan out into disproportionate downstream work, so that the invariant "Work performed is proportional to the size and fee of the input that triggered it." breaks and the result is DoS?

## Target
- File/function: `runtime/src/bank_forks.rs` -> `get_non_rooted()` (around line 724)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: a key that exists on an ancestor fork but not the current one
- Exploit idea: Send the smallest accepted input that makes `get_non_rooted` fan out into large downstream work, so a cheap transaction/packet costs the node orders more.
- Invariant to test: Work performed is proportional to the size and fee of the input that triggered it.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Plot input bytes versus work done in `get_non_rooted`; assert the ratio is bounded by a constant.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. An unprivileged attacker can cheaply create accounts or writes that force disproportionate storage, index, scan, hashing, or background-cleanup work and exhaust node memory or disk below true transaction cost.
