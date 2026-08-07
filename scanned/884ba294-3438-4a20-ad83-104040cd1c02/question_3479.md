# Q3479: get_files_in amplifies a cheap input into expensive work (main.rs)

## Question
Can an unprivileged attacker entering through ordinary transactions that create, write, resize, close and reopen accounts the attacker pays for reach `get_files_in` in `accounts-db/store-tool/src/main.rs` with a lookup whose result is cached and then invalidated by the attacker's own write, and make a minimal accepted input to `get_files_in` fan out into disproportionate downstream work, so that the invariant "Work performed is proportional to the size and fee of the input that triggered it." breaks and the result is DoS?

## Target
- File/function: `accounts-db/store-tool/src/main.rs` -> `get_files_in()` (around line 169)
- Entrypoint: ordinary transactions that create, write, resize, close and reopen accounts the attacker pays for
- Attacker controls: a lookup whose result is cached and then invalidated by the attacker's own write
- Exploit idea: Send the smallest accepted input that makes `get_files_in` fan out into large downstream work, so a cheap transaction/packet costs the node orders more.
- Invariant to test: Work performed is proportional to the size and fee of the input that triggered it.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Plot input bytes versus work done in `get_files_in`; assert the ratio is bounded by a constant.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. An unprivileged attacker can cheaply create accounts or writes that force disproportionate storage, index, scan, hashing, or background-cleanup work and exhaust node memory or disk below true transaction cost.
