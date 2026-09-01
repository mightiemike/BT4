# Q1245: method-id upgrade without authority via `verify_method_id_security_council` (method_id_verifier.rs)

## Question
Can an unprivileged attacker who replays a genuinely council-signed method-id body at a height or chain of its choosing, controlling signature bytes and pubkey indices, drive `verify_method_id_security_council` in `crates/light-client-prover/src/circuit/method_id_verifier.rs` so that the method id inserted by `BatchProofMethodIdAccessor` and the id three council keys signed stop being the same value, breaking the invariant that method ids change only by authorised upgrade?

## Target
- File/function: `crates/light-client-prover/src/circuit/method_id_verifier.rs` -> `verify_method_id_security_council`
- Entrypoint: unprivileged party replays a genuinely council-signed method-id body at a height or chain of its choosing
- Attacker controls: signature bytes and pubkey indices
- Exploit idea: method-id upgrade without authority - reach `verify_method_id_security_council` from that entrypoint and force the divergence where the method id inserted by `BatchProofMethodIdAccessor` and the id three council keys signed stop being the same value; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: method ids change only by authorised upgrade
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe a crafted body and assert `verify_method_id_security_council` rejects it
