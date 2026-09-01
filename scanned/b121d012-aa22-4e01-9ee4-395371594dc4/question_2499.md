# Q2499: genesis config drift via `end_l2_block_inner` (stf_blueprint.rs)

## Question
Can an unprivileged attacker who sends transactions that maximise the state diff a commitment must carry, controlling which JMT keys are read and written, drive `end_l2_block_inner` in `crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/stf_blueprint.rs` so that the genesis state the node initialises and the genesis root the circuit is compiled against stop being equal, breaking the invariant that genesis is identical across all roles?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/stf_blueprint.rs` -> `end_l2_block_inner`
- Entrypoint: unprivileged party sends transactions that maximise the state diff a commitment must carry
- Attacker controls: which JMT keys are read and written
- Exploit idea: genesis config drift - reach `end_l2_block_inner` from that entrypoint and force the divergence where the genesis state the node initialises and the genesis root the circuit is compiled against stop being equal; the adjacent symbols in the same file that carry the value are `StfBlueprint`, `apply_sov_txs_inner`, `apply_sov_tx_inner`, `begin_l2_block_inner`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: genesis is identical across all roles
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: hash both and compare
