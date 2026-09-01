# Q2794: proof served to a bridge consumer via `get_l2_block_by_hash` (rpc.rs)

## Question
Can an unprivileged attacker who calls a ledger / node RPC method with out-of-range or reversed parameters, controlling range and index parameters, drive `get_l2_block_by_hash` in `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` so that the state root an RPC reports as final and the root a verified proof commits stop being the same, breaking the invariant that finality reported over RPC is proof-backed?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` -> `get_l2_block_by_hash`
- Entrypoint: unprivileged party calls a ledger / node RPC method with out-of-range or reversed parameters
- Attacker controls: range and index parameters
- Exploit idea: proof served to a bridge consumer - reach `get_l2_block_by_hash` from that entrypoint and force the divergence where the state root an RPC reports as final and the root a verified proof commits stop being the same; the adjacent symbols in the same file that carry the value are `check_if_l2_block_pruned`, `get_l2_block`, `get_l2_block_by_number`, `get_l2_blocks_range`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: finality reported over RPC is proof-backed
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: report finality before proof verification and assert refusal
