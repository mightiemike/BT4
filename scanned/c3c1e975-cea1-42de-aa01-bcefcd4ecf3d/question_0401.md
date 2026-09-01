# Q0401: guest method id selection via `get_batch_proof_elfs` (bitcoin.rs)

## Question
Can an unprivileged attacker who makes the proved range span a fork or method-id activation boundary, controlling the overlap between requested ranges, drive `get_batch_proof_elfs` in `bin/citrea/src/rollup/bitcoin.rs` so that the guest method id used to prove and the id the light client will verify against stop being the same, breaking the invariant that provers and verifiers agree on the circuit?

## Target
- File/function: `bin/citrea/src/rollup/bitcoin.rs` -> `get_batch_proof_elfs`
- Entrypoint: unprivileged party makes the proved range span a fork or method-id activation boundary
- Attacker controls: the overlap between requested ranges
- Exploit idea: guest method id selection - reach `get_batch_proof_elfs` from that entrypoint and force the divergence where the guest method id used to prove and the id the light client will verify against stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinRollup`, `create_rpc_methods`, `create_storage_manager`, `create_da_service`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: provers and verifiers agree on the circuit
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: prove across an activation boundary and assert verifiability
