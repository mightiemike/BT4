# Q1089: prev-hash chaining via `run_sequencer_commitments_in_da_slot` (verifier.rs)

## Question
Can an unprivileged attacker who sends a transaction at an exact fork activation height, controlling which JMT keys are read and written, drive `run_sequencer_commitments_in_da_slot` in `crates/citrea-stf/src/verifier.rs` so that the previous L2 block hash the STF enforces and the hash the stored chain records stop being equal, breaking the invariant that L2 blocks form a hash chain with no forks?

## Target
- File/function: `crates/citrea-stf/src/verifier.rs` -> `run_sequencer_commitments_in_da_slot`
- Entrypoint: unprivileged party sends a transaction at an exact fork activation height
- Attacker controls: which JMT keys are read and written
- Exploit idea: prev-hash chaining - reach `run_sequencer_commitments_in_da_slot` from that entrypoint and force the divergence where the previous L2 block hash the STF enforces and the hash the stored chain records stop being equal; the adjacent symbols in the same file that carry the value are `StateTransitionVerifier`, `get_last_l1_hash_on_contract`, `borsh_deserialize_value`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: L2 blocks form a hash chain with no forks
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: insert a block with a mismatched parent and assert rejection
