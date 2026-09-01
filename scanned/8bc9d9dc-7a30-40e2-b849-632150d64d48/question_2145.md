# Q2145: activation height front-running via `run_l1_block` (mod.rs)

## Question
Can an unprivileged attacker who replays a genuinely council-signed method-id body at a height or chain of its choosing, controlling signature bytes and pubkey indices, drive `run_l1_block` in `crates/light-client-prover/src/circuit/mod.rs` so that the activation height the council intended and the height the circuit finally stores stop being the same, breaking the invariant that an authorised upgrade cannot be pre-empted by a replay?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `run_l1_block`
- Entrypoint: unprivileged party replays a genuinely council-signed method-id body at a height or chain of its choosing
- Attacker controls: signature bytes and pubkey indices
- Exploit idea: activation height front-running - reach `run_l1_block` from that entrypoint and force the divergence where the activation height the council intended and the height the circuit finally stores stop being the same; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an authorised upgrade cannot be pre-empted by a replay
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: replay a signed body early and assert the genuine one still applies
