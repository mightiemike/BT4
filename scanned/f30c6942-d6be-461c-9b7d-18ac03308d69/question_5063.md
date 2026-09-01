# Q5063: state diff accumulation via `get_last_l1_hash_on_contract` (verifier.rs)

## Question
Can an unprivileged attacker who sends transactions crafted so the native witness and the guest replay diverge, controlling which JMT keys are read and written, drive `get_last_l1_hash_on_contract` in `crates/citrea-stf/src/verifier.rs` so that the state diff the circuit outputs and the diff the DA blob carries stop being the same diff, breaking the invariant that published diffs equal proved diffs?

## Target
- File/function: `crates/citrea-stf/src/verifier.rs` -> `get_last_l1_hash_on_contract`
- Entrypoint: unprivileged party sends transactions crafted so the native witness and the guest replay diverge
- Attacker controls: which JMT keys are read and written
- Exploit idea: state diff accumulation - reach `get_last_l1_hash_on_contract` from that entrypoint and force the divergence where the state diff the circuit outputs and the diff the DA blob carries stop being the same diff; the adjacent symbols in the same file that carry the value are `StateTransitionVerifier`, `run_sequencer_commitments_in_da_slot`, `borsh_deserialize_value`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: published diffs equal proved diffs
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: diff the published blob against the circuit output
