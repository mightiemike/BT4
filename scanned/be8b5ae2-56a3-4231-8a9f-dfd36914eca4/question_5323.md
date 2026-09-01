# Q5323: prev-hash chaining via `apply_sov_tx_inner` (stf_blueprint.rs)

## Question
Can an unprivileged attacker who sends a transaction at an exact fork activation height, controlling the size and shape of the state diff, drive `apply_sov_tx_inner` in `crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/stf_blueprint.rs` so that the previous L2 block hash the STF enforces and the hash the stored chain records stop being equal, breaking the invariant that L2 blocks form a hash chain with no forks?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/stf_blueprint.rs` -> `apply_sov_tx_inner`
- Entrypoint: unprivileged party sends a transaction at an exact fork activation height
- Attacker controls: the size and shape of the state diff
- Exploit idea: prev-hash chaining - reach `apply_sov_tx_inner` from that entrypoint and force the divergence where the previous L2 block hash the STF enforces and the hash the stored chain records stop being equal; the adjacent symbols in the same file that carry the value are `StfBlueprint`, `apply_sov_txs_inner`, `begin_l2_block_inner`, `end_l2_block_inner`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: L2 blocks form a hash chain with no forks
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: insert a block with a mismatched parent and assert rejection
