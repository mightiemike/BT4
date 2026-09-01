# Q0511: recursion/aggregation boundary via `get_batch_proof_code_commitments` (bitcoin.rs)

## Question
Can an unprivileged attacker who submits load that forces concurrent proving sessions over overlapping ranges, controlling the overlap between requested ranges, drive `get_batch_proof_code_commitments` in `bin/citrea/src/rollup/bitcoin.rs` so that the set of sub-proofs aggregated and the set the output claims stop being the same set, breaking the invariant that aggregation is complete and exact?

## Target
- File/function: `bin/citrea/src/rollup/bitcoin.rs` -> `get_batch_proof_code_commitments`
- Entrypoint: unprivileged party submits load that forces concurrent proving sessions over overlapping ranges
- Attacker controls: the overlap between requested ranges
- Exploit idea: recursion/aggregation boundary - reach `get_batch_proof_code_commitments` from that entrypoint and force the divergence where the set of sub-proofs aggregated and the set the output claims stop being the same set; the adjacent symbols in the same file that carry the value are `BitcoinRollup`, `create_rpc_methods`, `create_storage_manager`, `create_da_service`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: aggregation is complete and exact
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: drop a sub-proof and assert the aggregate fails
