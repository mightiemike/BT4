# Q1671: proof session reuse via `create_da_service` (bitcoin.rs)

## Question
Can an unprivileged attacker who submits load that forces concurrent proving sessions over overlapping ranges, controlling the overlap between requested ranges, drive `create_da_service` in `bin/citrea/src/rollup/bitcoin.rs` so that the input a proving session was started for and the input its output is attributed to stop being the same, breaking the invariant that each proof output is bound to its input?

## Target
- File/function: `bin/citrea/src/rollup/bitcoin.rs` -> `create_da_service`
- Entrypoint: unprivileged party submits load that forces concurrent proving sessions over overlapping ranges
- Attacker controls: the overlap between requested ranges
- Exploit idea: proof session reuse - reach `create_da_service` from that entrypoint and force the divergence where the input a proving session was started for and the input its output is attributed to stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinRollup`, `create_rpc_methods`, `create_storage_manager`, `get_batch_proof_elfs`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each proof output is bound to its input
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: interleave sessions and assert attribution
