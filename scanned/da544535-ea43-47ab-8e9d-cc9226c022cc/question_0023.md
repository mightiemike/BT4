# Q0023: signature index handling via `get_l2_genesis_root` (initial_values.rs)

## Question
Can an unprivileged attacker who replays a genuinely council-signed method-id body at a height or chain of its choosing, controlling the serialized body encoding, drive `get_l2_genesis_root` in `crates/light-client-prover/src/circuit/initial_values.rs` so that the pubkey set the signatures are checked against and the distinct council members required stop being the same set, breaking the invariant that three distinct authorised signers are required?

## Target
- File/function: `crates/light-client-prover/src/circuit/initial_values.rs` -> `get_l2_genesis_root`
- Entrypoint: unprivileged party replays a genuinely council-signed method-id body at a height or chain of its choosing
- Attacker controls: the serialized body encoding
- Exploit idea: signature index handling - reach `get_l2_genesis_root` from that entrypoint and force the divergence where the pubkey set the signatures are checked against and the distinct council members required stop being the same set; the adjacent symbols in the same file that carry the value are `InitialValueProvider`, `NonEmptySlice`, `initial_batch_proof_method_ids`, `batch_prover_da_public_key`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: three distinct authorised signers are required
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: submit duplicate/out-of-order/boundary indices and assert rejection
