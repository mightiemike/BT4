# Q2135: signature index handling via `run_l1_block` (mod.rs)

## Question
Can an unprivileged attacker who inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices, controlling activation height and chain id fields, drive `run_l1_block` in `crates/light-client-prover/src/circuit/mod.rs` so that the pubkey set the signatures are checked against and the distinct council members required stop being the same set, breaking the invariant that three distinct authorised signers are required?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `run_l1_block`
- Entrypoint: unprivileged party inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices
- Attacker controls: activation height and chain id fields
- Exploit idea: signature index handling - reach `run_l1_block` from that entrypoint and force the divergence where the pubkey set the signatures are checked against and the distinct council members required stop being the same set; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: three distinct authorised signers are required
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: submit duplicate/out-of-order/boundary indices and assert rejection
