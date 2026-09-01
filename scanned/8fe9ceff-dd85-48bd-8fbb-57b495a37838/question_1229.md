# Q1229: genesis config drift via `get_last_l1_hash_on_contract` (verifier.rs)

## Question
Can an unprivileged attacker who sends transactions that maximise the state diff a commitment must carry, controlling the fork-activation height it targets, drive `get_last_l1_hash_on_contract` in `crates/citrea-stf/src/verifier.rs` so that the genesis state the node initialises and the genesis root the circuit is compiled against stop being equal, breaking the invariant that genesis is identical across all roles?

## Target
- File/function: `crates/citrea-stf/src/verifier.rs` -> `get_last_l1_hash_on_contract`
- Entrypoint: unprivileged party sends transactions that maximise the state diff a commitment must carry
- Attacker controls: the fork-activation height it targets
- Exploit idea: genesis config drift - reach `get_last_l1_hash_on_contract` from that entrypoint and force the divergence where the genesis state the node initialises and the genesis root the circuit is compiled against stop being equal; the adjacent symbols in the same file that carry the value are `StateTransitionVerifier`, `run_sequencer_commitments_in_da_slot`, `borsh_deserialize_value`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: genesis is identical across all roles
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: hash both and compare
