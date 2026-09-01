# Q2309: fork boundary applied at different heights via `apply_sov_tx_inner` (stf_blueprint.rs)

## Question
Can an unprivileged attacker who sends transactions that maximise the state diff a commitment must carry, controlling the size and shape of the state diff, drive `apply_sov_tx_inner` in `crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/stf_blueprint.rs` so that the fork the native node applies at height N and the fork the circuit applies stop being the same, breaking the invariant that fork activation is a pure function of height?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/stf_blueprint.rs` -> `apply_sov_tx_inner`
- Entrypoint: unprivileged party sends transactions that maximise the state diff a commitment must carry
- Attacker controls: the size and shape of the state diff
- Exploit idea: fork boundary applied at different heights - reach `apply_sov_tx_inner` from that entrypoint and force the divergence where the fork the native node applies at height N and the fork the circuit applies stop being the same; the adjacent symbols in the same file that carry the value are `StfBlueprint`, `apply_sov_txs_inner`, `begin_l2_block_inner`, `end_l2_block_inner`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fork activation is a pure function of height
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: execute a boundary block both ways and diff
