# Q1189: fork boundary applied at different heights via `get_last_l1_hash_on_contract` (verifier.rs)

## Question
Can an unprivileged attacker who sends transactions crafted so the native witness and the guest replay diverge, controlling the size and shape of the state diff, drive `get_last_l1_hash_on_contract` in `crates/citrea-stf/src/verifier.rs` so that the fork the native node applies at height N and the fork the circuit applies stop being the same, breaking the invariant that fork activation is a pure function of height?

## Target
- File/function: `crates/citrea-stf/src/verifier.rs` -> `get_last_l1_hash_on_contract`
- Entrypoint: unprivileged party sends transactions crafted so the native witness and the guest replay diverge
- Attacker controls: the size and shape of the state diff
- Exploit idea: fork boundary applied at different heights - reach `get_last_l1_hash_on_contract` from that entrypoint and force the divergence where the fork the native node applies at height N and the fork the circuit applies stop being the same; the adjacent symbols in the same file that carry the value are `StateTransitionVerifier`, `run_sequencer_commitments_in_da_slot`, `borsh_deserialize_value`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fork activation is a pure function of height
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: execute a boundary block both ways and diff
