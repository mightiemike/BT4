# Q0935: signature index handling via `verify_method_id_security_council` (method_id_verifier.rs)

## Question
Can an unprivileged attacker who inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices, controlling the serialized body encoding, drive `verify_method_id_security_council` in `crates/light-client-prover/src/circuit/method_id_verifier.rs` so that the pubkey set the signatures are checked against and the distinct council members required stop being the same set, breaking the invariant that three distinct authorised signers are required?

## Target
- File/function: `crates/light-client-prover/src/circuit/method_id_verifier.rs` -> `verify_method_id_security_council`
- Entrypoint: unprivileged party inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices
- Attacker controls: the serialized body encoding
- Exploit idea: signature index handling - reach `verify_method_id_security_council` from that entrypoint and force the divergence where the pubkey set the signatures are checked against and the distinct council members required stop being the same set; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: three distinct authorised signers are required
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: submit duplicate/out-of-order/boundary indices and assert rejection
