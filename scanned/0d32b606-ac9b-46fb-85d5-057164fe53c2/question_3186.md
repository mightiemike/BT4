# Q3186: remaining_until_next_interval amplifies a cheap input into expensive work (stats.rs)

## Question
Can an unprivileged attacker entering through ordinary transactions that create, write, resize, close and reopen accounts the attacker pays for reach `remaining_until_next_interval` in `accounts-db/src/accounts_index/stats.rs` with a maximal instruction/account count that pushes the path to its declared limit, and make a minimal accepted input to `remaining_until_next_interval` fan out into disproportionate downstream work, so that the invariant "Work performed is proportional to the size and fee of the input that triggered it." breaks and the result is DoS?

## Target
- File/function: `accounts-db/src/accounts_index/stats.rs` -> `remaining_until_next_interval()` (around line 158)
- Entrypoint: ordinary transactions that create, write, resize, close and reopen accounts the attacker pays for
- Attacker controls: a maximal instruction/account count that pushes the path to its declared limit
- Exploit idea: Send the smallest accepted input that makes `remaining_until_next_interval` fan out into large downstream work, so a cheap transaction/packet costs the node orders more.
- Invariant to test: Work performed is proportional to the size and fee of the input that triggered it.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Plot input bytes versus work done in `remaining_until_next_interval`; assert the ratio is bounded by a constant.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. An unprivileged attacker can cheaply create accounts or writes that force disproportionate storage, index, scan, hashing, or background-cleanup work and exhaust node memory or disk below true transaction cost.
